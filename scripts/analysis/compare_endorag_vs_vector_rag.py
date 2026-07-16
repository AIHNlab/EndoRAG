#!/usr/bin/env python3
"""Compare agentic workflow vs single-pass vector RAG evaluation logs.

Runs the analysis from compare_agentic_vs_vector_rag.ipynb and writes a
markdown report with dataset summaries, regression cases, and per-question
deep dives.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

RAG_CTX_RE = re.compile(
    r"Source:\s*(?P<source>.+?)\nScore:\s*(?P<score>-?[\d.]+)\n(?:Query:\s*(?P<query>.*?)\n)?Content:\s*(?P<text>.*)",
    re.DOTALL,
)

DATASETS: dict[str, str] = {
    "AdrenalGlands": "Adrenal Glands",
    "ParathyroidGlandAndBoneDisease": "Parathyroid Gland and Bone Disease",
    "PituitaryGlandAndHypothalamus": "Pituitary Gland and Hypothalamus",
    "ReproductiveEndocrinology": "Reproductive Endocrinology",
    "ThyroidGland": "Thyroid Gland",
    "UKEU": "UKEU",
}

# Short aliases accepted by --datasets (e.g. adrenal, reproductive, ukeu).
DATASET_ALIASES: dict[str, str] = {
    "adrenal": "AdrenalGlands",
    "adrenalglands": "AdrenalGlands",
    "adrenal_glands": "AdrenalGlands",
    "parathyroid": "ParathyroidGlandAndBoneDisease",
    "pituitary": "PituitaryGlandAndHypothalamus",
    "reproductive": "ReproductiveEndocrinology",
    "reproductive_endocrinology": "ReproductiveEndocrinology",
    "thyroid": "ThyroidGland",
    "ukeu": "UKEU",
}


def resolve_dataset_keys(selection: list[str] | None) -> list[str]:
    """Resolve CLI dataset names/aliases to canonical dataset keys."""
    if not selection:
        return list(DATASETS.keys())

    resolved: list[str] = []
    for name in selection:
        key = name if name in DATASETS else DATASET_ALIASES.get(name.lower())
        if not key:
            known = ", ".join(sorted(set(DATASETS) | set(DATASET_ALIASES)))
            raise ValueError(f"Unknown dataset {name!r}. Known: {known}")
        if key not in resolved:
            resolved.append(key)
    return resolved

FAILURE_TAG_GUIDE = [
    (
        "retrieval:no_source_overlap / retrieval:low_chunk_overlap",
        "Flow retrieval query or domain routing differs from single-pass; "
        "check attempted_queries and routed_category.",
    ),
    (
        "retrieval:rag_has_unique_sources",
        "RAG surfaced a useful document the flow missed — consider anchor-only vs decomposed queries.",
    ),
    (
        "polarity:misclassified_as_standard",
        'Stem says "incorrect/except" but flow used standard polarity — fix analyze_mcq_stem / polarity propagation.',
    ),
    (
        "reasoning:wrongly_rejected_correct_option",
        "Evidence supported the right answer but reasoning marked it contradicted/unsupported.",
    ),
    (
        "reasoning:wrongly_supported_selected_option",
        "Flow confidently backed a wrong option — reasoning prompt or evidence grounding issue.",
    ),
    (
        "answerability:insufficient + follow_up:not_triggered",
        "Flow knew evidence was weak but did not run a follow-up retrieval round.",
    ),
    (
        "reasoning:likely_reasoning_error",
        "Chunks overlap with RAG yet answer differs — focus on reasoning stage, not retrieval.",
    ),
]

CHUNK_OVERLAP_THRESHOLD = 0.45


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def default_paths(repo_root: Path) -> tuple[Path, Path, Path, Path]:
    from endorag.analysis.artifact_resolver import get_resolver

    resolver = get_resolver()
    base = resolver.vector_base("nemotron-3-nano:30b-cloud")
    rag_dir = base / "LLM/Cosine_C512_100"
    flow_dir = base / "agentic_workflow_8B"
    export_dir = repo_root / "results" / "analysis_exports"
    report_path = export_dir / "agentic_vs_vector_rag_report.md"
    return base, rag_dir, flow_dir, report_path


def rag_log_path(rag_dir: Path, dataset_key: str) -> Path:
    if dataset_key == "UKEU":
        return rag_dir / (
            f"rerank_qwen8b_Qwen_Qwen3-Reranker-8B_{dataset_key}_"
            f"diabetesVectorTool512_100_nemotron-3-nano_30b-cloud_qwen3-embedding_8b_1.json"
        )
    return rag_dir / (
        f"rerank_qwen8b_Qwen_Qwen3-Reranker-8B_{dataset_key}_dataset_"
        f"diabetesVectorTool512_100_nemotron-3-nano_30b-cloud_qwen3-embedding_8b_1.json"
    )


def flow_log_path(flow_dir: Path, dataset_key: str) -> Path:
    return flow_dir / f"agentic_workflow_eval_{dataset_key}.json"


def normalize_letter(value: Any) -> str:
    return str(value or "").strip().lower()


def is_correct(record: dict) -> bool:
    for metric in record.get("metrics", []):
        if metric.get("metric") == "ExactMatch":
            return float(metric.get("score", 0)) == 1.0
    return normalize_letter(record.get("actual_output")) == normalize_letter(
        record.get("expected_output")
    )


def parse_rag_chunk(item: str | dict) -> dict:
    if isinstance(item, dict):
        return {
            "source": item.get("source", "Unknown"),
            "score": item.get("score"),
            "query": item.get("query"),
            "text": item.get("text", ""),
            "retrieval_round": item.get("retrieval_round", 1),
        }
    match = RAG_CTX_RE.search(str(item))
    if not match:
        return {
            "source": "Unknown",
            "score": None,
            "query": None,
            "text": str(item),
            "retrieval_round": 1,
        }
    return {
        "source": match.group("source").strip(),
        "score": float(match.group("score")) if match.group("score") else None,
        "query": (match.group("query") or "").strip() or None,
        "text": match.group("text").strip(),
        "retrieval_round": 1,
    }


def parse_flow_chunk(item: dict) -> dict:
    return {
        "source": item.get("source", "Unknown"),
        "score": item.get("score"),
        "query": item.get("query"),
        "text": item.get("text", ""),
        "retrieval_round": item.get("retrieval_round", 1),
        "domain": item.get("domain"),
    }


def normalize_source(source: str) -> str:
    return Path(str(source).replace("\\", "/")).name.strip().lower()


def text_similarity(a: str, b: str) -> float:
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:2000], b[:2000]).ratio()


def compare_retrieval(rag_record: dict, flow_record: dict) -> dict:
    rag_chunks = [parse_rag_chunk(c) for c in rag_record.get("retrieval_context", [])]
    flow_chunks = [
        parse_flow_chunk(c)
        for c in flow_record.get("retrieval_context", [])
        if isinstance(c, dict)
    ]

    rag_sources = {normalize_source(c["source"]) for c in rag_chunks}
    flow_sources = {normalize_source(c["source"]) for c in flow_chunks}
    source_overlap = rag_sources & flow_sources

    chunk_overlaps = []
    for rc in rag_chunks:
        best = max((text_similarity(rc["text"], fc["text"]) for fc in flow_chunks), default=0.0)
        chunk_overlaps.append(best)

    flow_rounds = sorted({c.get("retrieval_round", 1) for c in flow_chunks})

    return {
        "rag_chunk_count": len(rag_chunks),
        "flow_chunk_count": len(flow_chunks),
        "source_overlap_count": len(source_overlap),
        "rag_only_sources": sorted(rag_sources - flow_sources),
        "flow_only_sources": sorted(flow_sources - rag_sources),
        "avg_chunk_text_overlap": round(sum(chunk_overlaps) / len(chunk_overlaps), 3)
        if chunk_overlaps
        else 0.0,
        "min_chunk_text_overlap": round(min(chunk_overlaps), 3) if chunk_overlaps else 0.0,
        "flow_retrieval_rounds": flow_rounds,
        "flow_multi_round": len(flow_rounds) > 1 or max(flow_rounds, default=1) > 1,
        "rag_top_sources": [normalize_source(c["source"]) for c in rag_chunks[:3]],
        "flow_top_sources": [normalize_source(c["source"]) for c in flow_chunks[:3]],
    }


def extract_flow_diagnostics(flow_record: dict) -> dict:
    fd = flow_record.get("flow_diagnostics") or {}
    stage_outputs = fd.get("stage_outputs") or {}

    retrieval_data: dict = {}
    answerability: dict = {}
    retrieval_stage = stage_outputs.get("retrieve_and_validate_evidence", {}) or {}
    for skill in reversed(retrieval_stage.get("results", []) or []):
        if not isinstance(skill, dict):
            continue
        if (
            skill.get("skill_name")
            in {"retrieve_diabetes_evidence", "retrieve_evidence"}
            and not retrieval_data
        ):
            retrieval_data = skill.get("data") or {}
        if skill.get("skill_name") == "judge_answerability" and not answerability:
            answerability = (skill.get("data") or {}).get("answerability") or {}

    reasoning: dict = {}
    option_assessments: list = []
    reason_stage = stage_outputs.get("reason_and_compose_answer", {}) or {}
    for skill in reversed(reason_stage.get("results", []) or []):
        if skill.get("skill_name") != "reason_mcq_answer":
            continue
        mcq = (skill.get("data") or {}).get("mcq_answer") or {}
        reasoning = {
            "status": skill.get("status"),
            "summary": skill.get("summary"),
            "selected_answer": normalize_letter(mcq.get("selected_answer")),
            "confidence": mcq.get("confidence"),
            "rationale": mcq.get("rationale"),
            "limitations": mcq.get("limitations") or skill.get("limitations") or [],
            "reasoning_mode": (skill.get("data") or {}).get("reasoning_mode"),
            "prompt_variant": (skill.get("data") or {}).get("prompt_variant"),
            "reconciliation_notes": (skill.get("data") or {}).get("reconciliation_notes") or [],
            "verifier": (skill.get("data") or {}).get("verifier") or {},
            "agentic_selected_answer": (skill.get("data") or {}).get("agentic_selected_answer"),
            "baseline_selector": (skill.get("data") or {}).get("baseline_selector") or {},
            "baseline_selector_answer": (skill.get("data") or {}).get("baseline_selector_answer"),
            "arbiter": (skill.get("data") or {}).get("arbiter") or {},
            "arbiter_answer": (skill.get("data") or {}).get("arbiter_answer"),
            "arbiter_used": (skill.get("data") or {}).get("arbiter_used") or False,
            "candidate_decision": (skill.get("data") or {}).get("candidate_decision") or {},
            "candidate_scores": (skill.get("data") or {}).get("candidate_scores") or {},
            "candidate_decision_reasons": (skill.get("data") or {}).get("candidate_decision_reasons") or [],
            "final_decision_source": (skill.get("data") or {}).get("final_decision_source"),
            "selected_evidence_ids": (skill.get("data") or {}).get("selected_evidence_ids") or [],
        }
        option_assessments = mcq.get("option_assessments") or []
        break

    attempted_queries = retrieval_data.get("attempted_queries") or []
    final_response = fd.get("final_response") or {}
    shared_state = fd.get("shared_execution_state") or {}
    retrieval_state = shared_state.get("retrieval") or {}
    verifier = fd.get("verifier") or reasoning.get("verifier") or {}

    return {
        "polarity": fd.get("polarity"),
        "stem_type": fd.get("stem_type"),
        "routed_category": fd.get("routed_category"),
        "answerability_sufficient": answerability.get("sufficient"),
        "answerability_confidence": answerability.get("confidence"),
        "answerability_rationale": answerability.get("rationale"),
        "missing_anchors": answerability.get("missing_anchors") or [],
        "attempted_queries": attempted_queries,
        "follow_up_used": len(attempted_queries) > 1,
        "followup_triggers": fd.get("followup_triggers") or retrieval_state.get("followup_triggers") or [],
        "reasoning": reasoning,
        "reasoning_mode": fd.get("reasoning_mode") or reasoning.get("reasoning_mode"),
        "prompt_variant": fd.get("prompt_variant") or reasoning.get("prompt_variant"),
        "reconciliation_notes": fd.get("reconciliation_notes") or reasoning.get("reconciliation_notes") or [],
        "verifier": verifier,
        "agentic_selected_answer": fd.get("agentic_selected_answer") or reasoning.get("agentic_selected_answer"),
        "baseline_selector": fd.get("baseline_selector") or reasoning.get("baseline_selector") or {},
        "baseline_selector_answer": fd.get("baseline_selector_answer") or reasoning.get("baseline_selector_answer"),
        "arbiter": fd.get("arbiter") or reasoning.get("arbiter") or {},
        "arbiter_answer": fd.get("arbiter_answer") or reasoning.get("arbiter_answer"),
        "arbiter_used": fd.get("arbiter_used") or reasoning.get("arbiter_used") or False,
        "candidate_decision": fd.get("candidate_decision") or reasoning.get("candidate_decision") or {},
        "candidate_scores": fd.get("candidate_scores") or reasoning.get("candidate_scores") or {},
        "candidate_decision_reasons": fd.get("candidate_decision_reasons") or reasoning.get("candidate_decision_reasons") or [],
        "final_decision_source": fd.get("final_decision_source") or reasoning.get("final_decision_source"),
        "option_assessments": option_assessments,
        "validation_errors": fd.get("validation_errors") or [],
        "warnings": final_response.get("warnings") or [],
    }


def question_polarity_from_text(text: str) -> str:
    t = text.lower()
    if re.search(r"\b(except|incorrect|not correct|false|least likely|unlikely)\b", t):
        return "except"
    return "standard"


def classify_failure(row: dict) -> list[str]:
    tags: list[str] = []
    ret = row.get("retrieval_compare", {})
    diag = row.get("flow_diag", {})

    if ret.get("source_overlap_count", 0) == 0:
        tags.append("retrieval:no_source_overlap")
    elif ret.get("avg_chunk_text_overlap", 1) < 0.35:
        tags.append("retrieval:low_chunk_overlap")
    if ret.get("rag_only_sources"):
        tags.append("retrieval:rag_has_unique_sources")

    q_polarity = question_polarity_from_text(row.get("question", ""))
    flow_polarity = diag.get("polarity")
    if q_polarity == "except" and flow_polarity != "except":
        tags.append("polarity:misclassified_as_standard")
    if q_polarity == "except" and flow_polarity == "except":
        tags.append("polarity:except_question")

    if diag.get("answerability_sufficient") is False:
        tags.append("answerability:insufficient")
    if diag.get("missing_anchors"):
        tags.append("answerability:missing_anchors")
    if not diag.get("follow_up_used") and diag.get("answerability_sufficient") is False:
        tags.append("follow_up:not_triggered")
    if diag.get("followup_triggers"):
        tags.append("follow_up:triggered")

    reasoning = diag.get("reasoning") or {}
    if reasoning.get("status") == "failed":
        tags.append("reasoning:skill_failed")
    elif ret.get("avg_chunk_text_overlap", 0) >= 0.5 and ret.get("source_overlap_count", 0) > 0:
        tags.append("reasoning:likely_reasoning_error")
    if diag.get("reconciliation_notes"):
        tags.append("reasoning:reconciled")
    if diag.get("arbiter_used"):
        tags.append("decision:arbiter_used")
    if diag.get("final_decision_source"):
        tags.append(f"decision:{diag.get('final_decision_source')}")
    if diag.get("candidate_decision"):
        tags.append("decision:deterministic_candidate_selector")

    verifier = diag.get("verifier") or {}
    if verifier:
        verdict = verifier.get("verdict")
        if verdict and verdict != "supported":
            tags.append(f"verifier:{verdict}")
        if verifier.get("followup_used"):
            tags.append("verifier:followup_used")

    expected = normalize_letter(row.get("expected"))
    selected = normalize_letter((reasoning or {}).get("selected_answer") or row.get("flow_answer"))
    for opt in diag.get("option_assessments") or []:
        if normalize_letter(opt.get("letter")) == expected and opt.get("status") in {
            "contradicted",
            "uncertain",
        }:
            tags.append("reasoning:wrongly_rejected_correct_option")
        if (
            normalize_letter(opt.get("letter")) == selected
            and opt.get("status") == "supported"
            and selected != expected
        ):
            tags.append("reasoning:wrongly_supported_selected_option")

    if diag.get("validation_errors"):
        tags.append("workflow:validation_error")

    if not tags:
        tags.append("unknown:needs_manual_review")
    return tags


def failure_bucket(retrieval_compare: dict) -> str:
    if (
        retrieval_compare["avg_chunk_text_overlap"] >= CHUNK_OVERLAP_THRESHOLD
        and retrieval_compare["source_overlap_count"] > 0
    ):
        return "likely_reasoning"
    return "likely_retrieval"


def load_eval_log(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_comparison_rows(
    rag_dir: Path,
    flow_dir: Path,
    dataset_keys: list[str] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    for dataset_key in resolve_dataset_keys(dataset_keys):
        dataset_label = DATASETS[dataset_key]
        rag_log = load_eval_log(rag_log_path(rag_dir, dataset_key))
        flow_log = load_eval_log(flow_log_path(flow_dir, dataset_key))

        rag_by_q = {r["input"]: r for r in rag_log["results"]}
        flow_by_q = {r["input"]: r for r in flow_log["results"]}
        common = sorted(set(rag_by_q) & set(flow_by_q))

        rag_times = rag_log.get("summary", {}).get("timing", {}).get("per_question_seconds") or []
        flow_times = flow_log.get("summary", {}).get("timing", {}).get("per_question_seconds") or []

        for idx, question in enumerate(common):
            rag_r = rag_by_q[question]
            flow_r = flow_by_q[question]
            rag_ok = is_correct(rag_r)
            flow_ok = is_correct(flow_r)

            if flow_ok and rag_ok:
                outcome = "both_correct"
            elif flow_ok and not rag_ok:
                outcome = "flow_right_rag_wrong"
            elif not flow_ok and rag_ok:
                outcome = "flow_wrong_rag_right"
            else:
                outcome = "both_wrong"

            row = {
                "dataset_key": dataset_key,
                "dataset_label": dataset_label,
                "question_idx": idx,
                "question": question,
                "expected": normalize_letter(rag_r.get("expected_output")),
                "rag_answer": normalize_letter(rag_r.get("actual_output")),
                "flow_answer": normalize_letter(flow_r.get("actual_output")),
                "rag_correct": rag_ok,
                "flow_correct": flow_ok,
                "outcome": outcome,
                "rag_time_s": rag_times[idx] if idx < len(rag_times) else None,
                "flow_time_s": flow_times[idx] if idx < len(flow_times) else None,
                "retrieval_compare": compare_retrieval(rag_r, flow_r),
                "flow_diag": extract_flow_diagnostics(flow_r),
                "rag_record": rag_r,
                "flow_record": flow_r,
                "rag_log_summary": rag_log["summary"],
                "flow_log_summary": flow_log["summary"],
            }
            if outcome == "flow_wrong_rag_right":
                row["failure_tags"] = classify_failure(row)
                row["failure_bucket"] = failure_bucket(row["retrieval_compare"])
            else:
                row["failure_tags"] = []
                row["failure_bucket"] = None
            rows.append(row)
    return rows


def build_dataset_summary(rows: list[dict], dataset_keys: list[str] | None = None) -> list[dict]:
    summary: list[dict] = []
    for dataset_key in resolve_dataset_keys(dataset_keys):
        dataset_label = DATASETS[dataset_key]
        sub = [r for r in rows if r["dataset_key"] == dataset_key]
        if not sub:
            continue
        rag_acc = sub[0]["rag_log_summary"]["overall_accuracy"]
        flow_acc = sub[0]["flow_log_summary"]["overall_accuracy"]
        rag_times = [r["rag_time_s"] for r in sub if r["rag_time_s"] is not None]
        flow_times = [r["flow_time_s"] for r in sub if r["flow_time_s"] is not None]
        summary.append(
            {
                "dataset": dataset_label,
                "n_questions": len(sub),
                "rag_accuracy": rag_acc,
                "flow_accuracy": flow_acc,
                "delta_flow_minus_rag": flow_acc - rag_acc,
                "flow_wrong_rag_right": sum(1 for r in sub if r["outcome"] == "flow_wrong_rag_right"),
                "flow_right_rag_wrong": sum(1 for r in sub if r["outcome"] == "flow_right_rag_wrong"),
                "both_wrong": sum(1 for r in sub if r["outcome"] == "both_wrong"),
                "both_correct": sum(1 for r in sub if r["outcome"] == "both_correct"),
                "avg_rag_time_s": round(sum(rag_times) / len(rag_times), 2) if rag_times else None,
                "avg_flow_time_s": round(sum(flow_times) / len(flow_times), 2) if flow_times else None,
            }
        )
    return sorted(summary, key=lambda x: x["delta_flow_minus_rag"])


def md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(md_escape(str(cell)) for cell in row) + " |")
    return "\n".join(lines)


def format_chunks_markdown(chunks: list[dict], label: str, limit: int = 5) -> str:
    lines = [f"#### {label} ({min(len(chunks), limit)} shown)"]
    for i, chunk in enumerate(chunks[:limit], 1):
        preview = (chunk.get("text") or "")[:400].replace("\n", " ")
        lines.append(
            f"{i}. **{chunk.get('source')}** — score={chunk.get('score')}, "
            f"round={chunk.get('retrieval_round', 1)}\n"
            f"   > {preview}…"
        )
    return "\n".join(lines)


def render_case_markdown(row: dict, case_num: int, show_chunks: int = 5) -> str:
    ret = row["retrieval_compare"]
    diag = row["flow_diag"]
    reasoning = diag.get("reasoning") or {}
    q = row["question"]

    parts = [
        f"### Case {case_num}: {row['dataset_label']}",
        "",
        f"- **Expected:** {row['expected'].upper()}",
        f"- **RAG answer:** {row['rag_answer'].upper()} (correct)",
        f"- **Flow answer:** {row['flow_answer'].upper()} (wrong)",
        f"- **Failure bucket:** {row.get('failure_bucket', 'n/a')}",
        f"- **Failure tags:** {', '.join(row['failure_tags'])}",
        f"- **Question polarity (heuristic):** {question_polarity_from_text(q)}",
        f"- **Flow detected polarity:** {diag.get('polarity')}",
        f"- **Retrieval overlap:** {ret['source_overlap_count']} shared sources, "
        f"avg text similarity={ret['avg_chunk_text_overlap']}, rounds={ret['flow_retrieval_rounds']}",
        f"- **Answerability:** sufficient={diag.get('answerability_sufficient')}, "
        f"confidence={diag.get('answerability_confidence')}",
        f"- **Reasoning confidence:** {reasoning.get('confidence')}",
        "",
        "**Answerability rationale:**",
        f"> {diag.get('answerability_rationale') or '—'}",
        "",
        "**Reasoning rationale:**",
        f"> {reasoning.get('rationale') or '—'}",
        "",
        "<details>",
        "<summary>Full question</summary>",
        "",
        "```",
        q,
        "```",
        "",
        "</details>",
        "",
    ]

    if diag.get("option_assessments"):
        opt_rows = []
        for opt in diag["option_assessments"]:
            letter = str(opt.get("letter", "")).upper()
            mark = " (expected)" if letter == row["expected"].upper() else ""
            opt_rows.append(
                [
                    f"{letter}{mark}",
                    opt.get("status", ""),
                    str(opt.get("rationale", ""))[:180],
                ]
            )
        parts.extend(
            [
                "**Option assessments (flow):**",
                "",
                md_table(["Option", "Status", "Rationale"], opt_rows),
                "",
            ]
        )

    rag_chunks = [parse_rag_chunk(c) for c in row["rag_record"].get("retrieval_context", [])]
    flow_chunks = [
        parse_flow_chunk(c)
        for c in row["flow_record"].get("retrieval_context", [])
        if isinstance(c, dict)
    ]
    parts.append(format_chunks_markdown(rag_chunks, "RAG retrieval context", show_chunks))
    parts.append("")
    parts.append(format_chunks_markdown(flow_chunks, "Flow retrieval context", show_chunks))
    parts.append("")

    if ret["rag_only_sources"] or ret["flow_only_sources"]:
        parts.extend(
            [
                f"- **RAG-only sources:** {', '.join(ret['rag_only_sources']) or '—'}",
                f"- **Flow-only sources:** {', '.join(ret['flow_only_sources']) or '—'}",
                "",
            ]
        )

    if diag.get("attempted_queries"):
        parts.append("**Attempted retrieval queries (flow):**")
        for i, query in enumerate(diag["attempted_queries"], 1):
            parts.append(f"{i}. `{query[:220]}…`")
        parts.append("")

    parts.append("---")
    parts.append("")
    return "\n".join(parts)


def render_markdown_report(
    rows: list[dict],
    summary: list[dict],
    *,
    base_dir: Path,
    dataset_keys: list[str] | None = None,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    regressions = [r for r in rows if r["outcome"] == "flow_wrong_rag_right"]
    regressions.sort(key=lambda r: (r["dataset_label"], r["retrieval_compare"]["avg_chunk_text_overlap"]))

    tag_counter: Counter[str] = Counter()
    for r in regressions:
        tag_counter.update(r["failure_tags"])

    outcome_totals = Counter(r["outcome"] for r in rows)
    bucket_totals = Counter(r.get("failure_bucket") for r in regressions)

    underperforming = [s["dataset"] for s in summary if s["delta_flow_minus_rag"] < 0]
    selected = resolve_dataset_keys(dataset_keys)
    dataset_names = ", ".join(DATASETS[k] for k in selected)

    parts = [
        "# Agentic Workflow vs Single-Pass Vector RAG — Analysis Report",
        "",
        f"**Generated:** {generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Log base:** `{base_dir}`",
        f"**Datasets:** {dataset_names}",
        f"**Aligned question pairs:** {len(rows)} across {len(selected)} datasets",
        "",
        "## Executive summary",
        "",
        f"- **Total regressions** (flow wrong, RAG right): **{len(regressions)}**",
        f"- **Flow wins** (flow right, RAG wrong): **{outcome_totals['flow_right_rag_wrong']}**",
        f"- **Both wrong:** **{outcome_totals['both_wrong']}**",
        f"- **Both correct:** **{outcome_totals['both_correct']}**",
        "",
    ]

    if underperforming:
        parts.append(
            f"Datasets where flow accuracy is below RAG: **{', '.join(underperforming)}**."
        )
    else:
        parts.append("No dataset has flow accuracy below RAG.")
    parts.append("")

    if tag_counter:
        top_tag = tag_counter.most_common(1)[0]
        parts.append(
            f"Most frequent regression tag: `{top_tag[0]}` ({top_tag[1]} cases). "
            "This often points to reasoning-stage failures rather than retrieval gaps."
        )
        parts.append("")

    parts.extend(
        [
            "## 1. Accuracy by dataset",
            "",
            md_table(
                [
                    "Dataset",
                    "N",
                    "RAG acc",
                    "Flow acc",
                    "Δ (flow−rag)",
                    "Flow wrong / RAG right",
                    "Flow right / RAG wrong",
                    "Both wrong",
                    "Avg RAG time (s)",
                    "Avg flow time (s)",
                ],
                [
                    [
                        s["dataset"],
                        s["n_questions"],
                        f"{s['rag_accuracy']:.1%}",
                        f"{s['flow_accuracy']:.1%}",
                        f"{s['delta_flow_minus_rag']:+.1%}",
                        s["flow_wrong_rag_right"],
                        s["flow_right_rag_wrong"],
                        s["both_wrong"],
                        s["avg_rag_time_s"],
                        s["avg_flow_time_s"],
                    ]
                    for s in summary
                ],
            ),
            "",
            "## 2. Outcome breakdown (all questions)",
            "",
            md_table(
                ["Outcome", "Count", "Share"],
                [
                    [name, count, f"{100 * count / len(rows):.1f}%"]
                    for name, count in sorted(outcome_totals.items(), key=lambda x: -x[1])
                ],
            ),
            "",
            "## 3. Regression failure tags",
            "",
        ]
    )

    if tag_counter:
        parts.append(
            md_table(
                ["Failure tag", "Count"],
                [[tag, count] for tag, count in tag_counter.most_common()],
            )
        )
    else:
        parts.append("_No regressions found._")
    parts.append("")

    parts.extend(
        [
            "## 4. Retrieval vs reasoning split (regressions only)",
            "",
            f"Threshold: avg chunk text overlap ≥ {CHUNK_OVERLAP_THRESHOLD} and shared sources > 0 → likely reasoning.",
            "",
            md_table(
                ["Bucket", "Count"],
                [[bucket, count] for bucket, count in bucket_totals.items() if bucket],
            ),
            "",
        ]
    )

    if regressions:
        parts.extend(
            [
                "## 5. Regression summary table",
                "",
                md_table(
                    [
                        "#",
                        "Dataset",
                        "Expected",
                        "RAG",
                        "Flow",
                        "Bucket",
                        "Overlap",
                        "Tags",
                    ],
                    [
                        [
                            i + 1,
                            r["dataset_label"],
                            r["expected"].upper(),
                            r["rag_answer"].upper(),
                            r["flow_answer"].upper(),
                            r.get("failure_bucket", ""),
                            r["retrieval_compare"]["avg_chunk_text_overlap"],
                            ", ".join(r["failure_tags"][:3]),
                        ]
                        for i, r in enumerate(regressions)
                    ],
                ),
                "",
                "## 6. Per-case deep dives",
                "",
            ]
        )
        for i, row in enumerate(regressions, 1):
            parts.append(render_case_markdown(row, i))
    else:
        parts.append("## 5. Per-case deep dives\n\n_No regressions to report._\n")

    parts.extend(
        [
            "## 7. Interpreting failure tags",
            "",
            md_table(["Tag", "Likely fix direction"], FAILURE_TAG_GUIDE),
            "",
            "## 8. Recommended next steps",
            "",
            "1. Prioritise datasets with negative Δ (flow worse than RAG).",
            "2. For `likely_reasoning` cases, inspect option assessments and polarity handling.",
            "3. For `likely_retrieval` cases, compare attempted queries and RAG-only sources.",
            "4. Annotate each regression with a manual root-cause before changing the workflow.",
            "",
        ]
    )

    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare agentic workflow vs vector RAG logs and write a markdown report."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=repo_root_from_script(),
        help="Repository root (default: parent of evaluate/)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default="configs/experiments/paper_analysis.yaml",
        help="Paper analysis manifest path.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Analysis export directory (default: results/analysis_exports).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown report output path (default: <output-dir>/agentic_vs_vector_rag_report.md)",
    )
    parser.add_argument(
        "--also-export-json",
        action="store_true",
        help="Also write flow_wrong_rag_right_full.json alongside the report",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=None,
        metavar="DATASET",
        help=(
            "Subset of datasets to analyse (keys or aliases). "
            "Examples: AdrenalGlands reproductive ukeu. Default: all datasets."
        ),
    )
    from endorag.analysis.cli_args import resolve_analysis_context

    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    if args.manifest:
        _, _manifest, default_output = resolve_analysis_context(args)
    else:
        default_output = repo_root / "results" / "analysis_exports"
    _, rag_dir, flow_dir, default_report = default_paths(repo_root)
    output_path = (args.output or default_report).resolve()
    if args.output_dir is not None:
        output_path = (Path(args.output_dir).resolve() / "agentic_vs_vector_rag_report.md")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dataset_keys = resolve_dataset_keys(args.datasets)
    for key in dataset_keys:
        rag_path = rag_log_path(rag_dir, key)
        flow_path = flow_log_path(flow_dir, key)
        if not rag_path.exists():
            raise FileNotFoundError(f"Missing RAG log: {rag_path}")
        if not flow_path.exists():
            raise FileNotFoundError(f"Missing flow log: {flow_path}")

    rows = build_comparison_rows(rag_dir, flow_dir, dataset_keys)
    summary = build_dataset_summary(rows, dataset_keys)
    log_base = rag_dir.parent.parent  # .../qwen3-embedding:8b
    try:
        log_base_display = log_base.relative_to(repo_root)
    except ValueError:
        log_base_display = log_base

    report = render_markdown_report(
        rows, summary, base_dir=log_base_display, dataset_keys=dataset_keys
    )
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote markdown report: {output_path}")

    if args.also_export_json:
        regressions = [r for r in rows if r["outcome"] == "flow_wrong_rag_right"]
        json_path = output_path.parent / "flow_wrong_rag_right_full.json"
        payload = [
            {
                "dataset": r["dataset_label"],
                "question": r["question"],
                "expected": r["expected"],
                "rag_answer": r["rag_answer"],
                "flow_answer": r["flow_answer"],
                "failure_tags": r["failure_tags"],
                "failure_bucket": r.get("failure_bucket"),
                "retrieval_compare": r["retrieval_compare"],
                "flow_diag": {
                    k: v for k, v in r["flow_diag"].items() if k != "stem_info"
                },
            }
            for r in regressions
        ]
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote JSON export: {json_path}")

    regressions = [r for r in rows if r["outcome"] == "flow_wrong_rag_right"]
    print(f"Aligned pairs: {len(rows)} | Regressions (flow wrong, RAG right): {len(regressions)}")


if __name__ == "__main__":
    main()
