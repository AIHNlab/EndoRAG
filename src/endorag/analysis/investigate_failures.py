"""Reproducible investigation of two questions for the EndoRAG paper.

Point 1: Failure modes of the agentic workflow (EndoRAG) for the best model (gemma4).
Point 2: Whether "agentic == LLM-only / single-pass RAG" ties (e.g. minimax on the
         Reproductive dataset) could be answer-key contamination.

Everything is recomputed directly from the raw per-question prediction JSONs under
``evaluate/Method_vectorRag`` and ``evaluate/Method_LLM`` so the numbers are
verifiable and independent of the earlier hand-written markdown reports.

Usage:
    python evaluate/investigate_failures_and_contamination.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from endorag.analysis.artifact_resolver import RERANK_PREFIX, get_resolver
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context

EMBED = "qwen3-embedding:8b"
RERANKER_TAG = RERANK_PREFIX.rstrip("_")

# dataset label -> (agentic file stem, RAG filename token, LLM filename token)
# NOTE: the Diabetes set is stored under the "MCQs_sample_questions2015_full" (RAG)
# and "MCQs_book" (LLM) names — verified by exact question-text overlap (53/53).
DATASETS = {
    "Diabetes": ("Diabetes", "MCQs_sample_questions2015_full", "MCQs_book_LLM"),
    "Thyroid": ("ThyroidGland", "ThyroidGland_dataset", "ThyroidGland_LLM"),
    "Parathyroid": ("ParathyroidGlandAndBoneDisease", "ParathyroidGlandAndBoneDisease_dataset", "ParathyroidGlandAndBoneDisease_LLM"),
    "Pituitary": ("PituitaryGlandAndHypothalamus", "PituitaryGlandAndHypothalamus_dataset", "PituitaryGlandAndHypothalamus_LLM"),
    "Adrenal": ("AdrenalGlands", "AdrenalGlands_dataset", "AdrenalGlands_LLM"),
    "Reproductive": ("ReproductiveEndocrinology", "ReproductiveEndocrinology_dataset", "ReproductiveEndocrinology_LLM"),
    "UKEU": ("UKEU", "UKEU_diabetesVectorTool", "UKEU_LLM"),
}

MONOTOPIC = {"Diabetes", "Thyroid", "Parathyroid", "Pituitary", "Adrenal", "Reproductive"}


def norm_q(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def norm_ans(text: str) -> str:
    return str(text or "").strip().lower()


def load_results(path: Path) -> dict[str, dict]:
    """Return {normalized_question: result_record}. Empty dict if file missing."""
    if path is None or not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for r in data.get("results", []):
        out[norm_q(r.get("input", ""))] = r
    return out


def find_one(root: Path, must_contain: list[str], must_not: list[str] | None = None) -> Path | None:
    must_not = must_not or []
    best = None
    for p in root.rglob("*.json"):
        s = str(p)
        if all(tok in s for tok in must_contain) and not any(tok in s for tok in must_not):
            # prefer the shortest path (avoids oracle/literature variants when possible)
            if best is None or len(s) < len(str(best)):
                best = p
    return best


def model_slug(model: str) -> str:
    # gemma4:31b-cloud -> gemma4_31b-cloud (as used in baseline filenames)
    return model.replace(":", "_")


def agentic_path(model: str, stem: str) -> Path | None:
    path = get_resolver().find_agentic_file(model, stem)
    return path


def rag_paths(model: str, token: str) -> tuple[Path | None, Path | None]:
    """(no_rerank, rerank) single-pass vector RAG files for this model/dataset."""
    resolver = get_resolver()
    prefixes = (token,)
    no_rerank = resolver.find_vector_rag_file(model, prefixes, with_rerank=False)
    rerank = resolver.find_vector_rag_file(model, prefixes, with_rerank=True)
    return no_rerank, rerank


def llm_path(model: str, llm_token: str) -> Path | None:
    slug = llm_token.replace("_LLM", "")
    return get_resolver().find_llm_only_file(model, slug)


def is_correct(rec: dict) -> bool | None:
    if not rec:
        return None
    return norm_ans(rec.get("actual_output")) == norm_ans(rec.get("expected_output"))


def diag(rec: dict) -> dict:
    return rec.get("flow_diagnostics", {}) or {}


def answerability_of(rec: dict) -> dict:
    """Replicate evaluate_exam._latest_agentic_answerability on saved diagnostics."""
    stage = (diag(rec).get("stage_outputs") or {}).get("retrieve_and_validate_evidence", {})
    for skill in reversed(stage.get("results", []) or []):
        if skill.get("skill_name") == "judge_answerability":
            a = (skill.get("data") or {}).get("answerability")
            if isinstance(a, dict):
                return a
    return {}


def followup_used(rec: dict) -> bool:
    d = diag(rec)
    if d.get("followup_triggers"):
        return True
    return (d.get("reasoning_mode") or "") == "followup"


def retrieval_sources(rec: dict) -> set[str]:
    out = set()
    for ctx in rec.get("retrieval_context", []) or []:
        if isinstance(ctx, dict):
            md = ctx.get("metadata") or {}
            out.add(md.get("file_path") or ctx.get("source") or "?")
        elif isinstance(ctx, str):
            m = re.search(r"Source:\s*(.+)", ctx)
            if m:
                out.add(m.group(1).strip())
    return out


# --------------------------------------------------------------------------- #
# Point 1: Gemma failure modes
# --------------------------------------------------------------------------- #
ANSWER_KEY_PATTERNS = [
    "answer_key", "answerkey", "answer-key", "expected_output", "gold", "solution",
    "questionbank", "question_bank", "mcq_answers", "exam_key", "correct_answers",
]


def investigate_gemma_failures(model="gemma4:31b-cloud"):
    print("=" * 78)
    print(f"POINT 1 — Failure modes of the agentic workflow (EndoRAG), model={model}")
    print("=" * 78)

    per_dataset = {}
    all_wrong = []
    decision_source_all = Counter()
    routed_mode = {}

    for label, (stem, rag_token, llm_token) in DATASETS.items():
        ag = load_results(agentic_path(model, stem) or Path())
        if not ag:
            print(f"  [warn] no agentic file for {label}")
            continue
        no_rr = load_results(rag_paths(model, rag_token)[0])
        rr = load_results(rag_paths(model, rag_token)[1])
        llm = load_results(llm_path(model, llm_token))

        routed_counts = Counter(diag(r).get("routed_category") for r in ag.values())
        routed_mode[label] = routed_counts.most_common(1)[0][0] if routed_counts else None

        n = len(ag)
        n_correct = sum(1 for r in ag.values() if is_correct(r))
        wrong = [q for q, r in ag.items() if is_correct(r) is False]
        per_dataset[label] = {
            "n": n,
            "acc": n_correct / n if n else 0.0,
            "wrong": len(wrong),
            "rr_acc": (sum(1 for r in rr.values() if is_correct(r)) / len(rr)) if rr else None,
            "llm_acc": (sum(1 for r in llm.values() if is_correct(r)) / len(llm)) if llm else None,
        }

        for q, r in ag.items():
            decision_source_all[diag(r).get("final_decision_source")] += 1

        for q in wrong:
            r = ag[q]
            d = diag(r)
            ans = answerability_of(r)
            all_wrong.append({
                "dataset": label,
                "q": q,
                "expected": norm_ans(r.get("expected_output")),
                "endorag": norm_ans(r.get("actual_output")),
                "agentic_selected": norm_ans(d.get("agentic_selected_answer")),
                "baseline_selected": norm_ans(d.get("baseline_selector_answer")),
                "final_source": d.get("final_decision_source"),
                "routed": d.get("routed_category"),
                "answerability_sufficient": ans.get("sufficient"),
                "followup": followup_used(r),
                "llm_correct": is_correct(llm.get(q)) if q in llm else None,
                "rr_correct": is_correct(rr.get(q)) if q in rr else None,
                "no_rr_correct": is_correct(no_rr.get(q)) if q in no_rr else None,
            })

    total_n = sum(v["n"] for v in per_dataset.values())
    total_wrong = sum(v["wrong"] for v in per_dataset.values())
    print(f"\nAligned questions: {total_n}   Wrong (EndoRAG): {total_wrong}   "
          f"Accuracy: {(total_n - total_wrong) / total_n:.4f}")

    print("\nPer-dataset (EndoRAG acc | rerank-RAG acc | LLM-only acc | wrong):")
    for label, v in per_dataset.items():
        rr = f"{v['rr_acc']:.3f}" if v["rr_acc"] is not None else "  NA "
        lo = f"{v['llm_acc']:.3f}" if v["llm_acc"] is not None else "  NA "
        print(f"  {label:13s} {v['acc']:.3f} | {rr} | {lo} | {v['wrong']:>2d}/{v['n']}")

    # Overlap of wrong sets
    endorag_only = sum(1 for w in all_wrong if w["llm_correct"] and w["rr_correct"])
    wrong_with_llm = sum(1 for w in all_wrong if w["llm_correct"] is False)
    wrong_with_rr = sum(1 for w in all_wrong if w["rr_correct"] is False)
    all_three = sum(1 for w in all_wrong if w["llm_correct"] is False and w["rr_correct"] is False)
    print("\nFailure overlap:")
    print(f"  EndoRAG wrong & LLM-only wrong ......... {wrong_with_llm}")
    print(f"  EndoRAG wrong & rerank-RAG wrong ....... {wrong_with_rr}")
    print(f"  EndoRAG, LLM-only & rerank-RAG wrong ... {all_three}")
    print(f"  EndoRAG-only wrong (others correct) ... {endorag_only}")

    # Automatic failure signals (data-driven; complements manual clinical categories)
    routing_mismatch = 0
    insufficient = 0
    retrieval_gap = 0
    reasoning_error = 0
    for w in all_wrong:
        mono = w["dataset"] in MONOTOPIC
        mism = mono and w["routed"] != routed_mode.get(w["dataset"])
        insuf = (w["answerability_sufficient"] is False) or w["followup"]
        gap = bool(w["rr_correct"])  # rerank RAG got it right, EndoRAG did not
        if mism:
            routing_mismatch += 1
        if insuf:
            insufficient += 1
        if gap:
            retrieval_gap += 1
        if not mism and not insuf and not gap:
            reasoning_error += 1
    print("\nAutomatic failure signals over the wrong set (non-exclusive):")
    print(f"  Incorrect domain routing (routed != dataset domain) . {routing_mismatch}")
    print(f"  Insufficient evidence (answerability flag / follow-up) {insufficient}")
    print(f"  Retrieval gap vs best single-pass rerank RAG ........ {retrieval_gap}")
    print(f"  Residual reasoning error (rel. evidence, no gap) .... {reasoning_error}")

    # Anchoring / decision source among wrong
    ds_wrong = Counter(w["final_source"] for w in all_wrong)
    final_eq_baseline = sum(1 for w in all_wrong if w["endorag"] == w["baseline_selected"])
    print("\nDecision source among WRONG cases:")
    for k, v in ds_wrong.most_common():
        print(f"  {str(k):28s} {v}")
    print(f"  final answer == internal baseline selector: {final_eq_baseline}/{len(all_wrong)}")

    print("\nDecision source over ALL cases:")
    for k, v in decision_source_all.most_common():
        print(f"  {str(k):28s} {v}")

    return all_wrong


# --------------------------------------------------------------------------- #
# Point 2: Contamination analysis for "agentic == baseline" ties
# --------------------------------------------------------------------------- #
def investigate_contamination(models=None):
    models = models or [
        "minimax-m2.7:cloud",
        "gemma4:31b-cloud",
        "nemotron-3-nano:30b-cloud",
        "mistral-small3.2:24b",
    ]
    print("\n" + "=" * 78)
    print("POINT 2 — Are agentic==LLM-only/RAG ties a sign of contamination?")
    print("=" * 78)

    for model in models:
        print(f"\n### Model: {model}")
        print(f"{'dataset':13s} {'agentic':>7s} {'norr':>6s} {'rerank':>7s} {'llm':>5s} "
              f"{'ag=base':>7s} {'ag=rr':>6s} {'all4=':>6s}")
        for label, (stem, rag_token, llm_token) in DATASETS.items():
            ag = load_results(agentic_path(model, stem) or Path())
            if not ag:
                continue
            no_rr, rr = (load_results(p) for p in rag_paths(model, rag_token))
            llm = load_results(llm_path(model, llm_token))

            def acc(d):
                vals = [is_correct(r) for r in d.values()]
                vals = [v for v in vals if v is not None]
                return sum(vals) / len(vals) if vals else None

            n = 0
            ag_eq_base = ag_eq_rr = all_same = 0
            for q, r in ag.items():
                d = diag(r)
                final = norm_ans(r.get("actual_output"))
                base = norm_ans(d.get("baseline_selector_answer"))
                if base:
                    n += 1
                    if final == base:
                        ag_eq_base += 1
                if q in rr and final == norm_ans(rr[q].get("actual_output")):
                    ag_eq_rr += 1
                if q in rr and q in no_rr and q in llm:
                    if len({final, norm_ans(rr[q].get('actual_output')),
                            norm_ans(no_rr[q].get('actual_output')),
                            norm_ans(llm[q].get('actual_output'))}) == 1:
                        all_same += 1

            def pct(x, d):
                return f"{100*x/d:5.1f}" if d else "  NA "

            a = acc(ag)
            print(f"{label:13s} "
                  f"{(f'{100*a:5.1f}' if a is not None else '  NA '):>7s} "
                  f"{(f'{100*acc(no_rr):5.1f}' if acc(no_rr) is not None else '  NA '):>6s} "
                  f"{(f'{100*acc(rr):5.1f}' if acc(rr) is not None else '  NA '):>7s} "
                  f"{(f'{100*acc(llm):5.1f}' if acc(llm) is not None else '  NA '):>5s} "
                  f"{pct(ag_eq_base, len(ag)):>7s} "
                  f"{pct(ag_eq_rr, len(ag)):>6s} "
                  f"{pct(all_same, len(ag)):>6s}")

    # Focused contamination scan on the flagged example: minimax / Reproductive
    print("\n--- Contamination scan: minimax-m2.7:cloud / Reproductive ---")
    scan_case("minimax-m2.7:cloud", "Reproductive")


def scan_case(model, label):
    stem, rag_token, llm_token = DATASETS[label]
    ag = load_results(agentic_path(model, stem) or Path())
    no_rr, rr = (load_results(p) for p in rag_paths(model, rag_token))
    llm = load_results(llm_path(model, llm_token))

    # 1) Does any retrieved source look like an answer key / question bank?
    all_sources = set()
    for r in ag.values():
        all_sources |= retrieval_sources(r)
    suspicious = sorted(
        s for s in all_sources
        if any(p in s.lower() for p in ANSWER_KEY_PATTERNS)
    )
    print(f"  distinct retrieval sources: {len(all_sources)}")
    print(f"  answer-key/question-bank-like sources: "
          f"{suspicious if suspicious else 'NONE'}")
    for s in sorted(all_sources):
        print(f"    - {s}")

    # 2) Is the model already correct WITHOUT retrieval? (memorization signal)
    def acc(d):
        vals = [is_correct(r) for r in d.values() if is_correct(r) is not None]
        return sum(vals) / len(vals) if vals else float("nan")

    print(f"\n  Accuracy  LLM-only={acc(llm):.3f}  no-rerank RAG={acc(no_rr):.3f}  "
          f"rerank RAG={acc(rr):.3f}  agentic={acc(ag):.3f}")

    # 3) Per-question: how often do agentic/LLM/RAG all agree, and how often wrong-together?
    both_correct_same = 0
    same_answer = 0
    aligned = 0
    for q, r in ag.items():
        if q not in llm or q not in rr:
            continue
        aligned += 1
        a = norm_ans(r.get("actual_output"))
        l = norm_ans(llm[q].get("actual_output"))
        rrx = norm_ans(rr[q].get("actual_output"))
        if a == l == rrx:
            same_answer += 1
            if is_correct(r):
                both_correct_same += 1
    print(f"  aligned questions: {aligned}")
    print(f"  agentic==LLM-only==rerankRAG (same letter): {same_answer}/{aligned}")
    print(f"    ...of those, all correct: {both_correct_same}")
    print("  NOTE: run_diabetes_workflow(input_text, deps) receives ONLY the question; "
          "expected_output is attached later for scoring (see evaluate_exam.py).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_paper_analysis_args(parser)
    args = parser.parse_args()
    repo_root, _manifest, output_dir = resolve_analysis_context(args)
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "best_endorag_failure_modes_report.md"
    lines: list[str] = []
    original_stdout = sys.stdout

    class _Tee:
        def write(self, data: str) -> int:
            lines.append(data)
            return original_stdout.write(data)

        def flush(self) -> None:
            original_stdout.flush()

    sys.stdout = _Tee()
    try:
        investigate_gemma_failures()
        investigate_contamination()
    finally:
        sys.stdout = original_stdout

    report_path.write_text("".join(lines), encoding="utf-8")
    print(f"Wrote {report_path.relative_to(repo_root)}")
