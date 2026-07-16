"""Endocrine specialty labels and LLM routing used by exam eval and agent flows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

ROUTING_CATEGORIES = [
    "Diabetes and Lipid Metabolism",
    "Pituitary Gland and Hypothalamus",
    "Thyroid Gland",
    "Parathyroid Gland and Bone Disease",
    "Reproductive Endocrinology, Andrology and Sexual Function",
    "Adrenal Glands",
    "Other",
]

# Monotopic exam JSON files → fixed routing bucket (mixed exams omitted).
DATASET_FILE_TO_ROUTING_CATEGORY = {
    "ThyroidGland_dataset.json": "Thyroid Gland",
    "AdrenalGlands_dataset.json": "Adrenal Glands",
    "ParathyroidGlandAndBoneDisease_dataset.json": "Parathyroid Gland and Bone Disease",
    "PituitaryGlandAndHypothalamus_dataset.json": "Pituitary Gland and Hypothalamus",
    "ReproductiveEndocrinology_dataset.json": (
        "Reproductive Endocrinology, Andrology and Sexual Function"
    ),
}


def question_category_map_path_for_dataset(
    test_data_dir: str, repo_root: str | Path = "."
) -> str:
    """Oracle/eval map path: UKEU map for mixed UKEU exam, else MCQ book map."""
    base = os.path.basename(str(test_data_dir or "").strip())
    if base == "UKEU.json":
        return str(Path(repo_root) / "data/routing/ukeu_category_map.json")
    return str(Path(repo_root) / "data/routing/mcq_diabetes_category_map.json")


def infer_routing_category_from_dataset_path(test_data_dir: str) -> Optional[str]:
    """Expected routing category for monotopic *_dataset.json files (metrics ground truth)."""
    base = os.path.basename(str(test_data_dir or "").strip())
    cat = DATASET_FILE_TO_ROUTING_CATEGORY.get(base)
    if cat in ROUTING_CATEGORIES:
        return cat
    return None


def classify_endocrine_routing_category(llm: Any, input_text: str) -> str:
    """Classify *input_text* into exactly one of ``ROUTING_CATEGORIES`` using *llm*.complete."""
    numbered_categories = "\n".join(
        f"{i + 1}) {cat}" for i, cat in enumerate(ROUTING_CATEGORIES)
    )
    n = len(ROUTING_CATEGORIES)

    classification_prompt = (
        f"You assign each question to exactly one routing bucket. The bucket definitions are "
        f"only the numbered titles below—use their ordinary clinical meaning.\n\n"
        f"BUCKETS:\n{numbered_categories}\n\n"
        f"QUESTION:\n{input_text}\n\n"
        f"OUTPUT: a single digit from 1 to {n}, nothing else (no words, spaces, or punctuation).\n\n"
        f"Selection rules (general):\n"
        f"- Pick the title that best matches the **primary** knowledge target of the stem: "
        f"the main disease, organ/system, or pathophysiology being assessed.\n"
        f"- Ignore incidental context (comorbidity, screening labs, background demographics) "
        f"unless the item clearly pivots the answer on that detail.\n"
        f"- If multiple areas appear, choose the single best headline—the axis the vignette "
        f"most directly tests (source gland/hormone pathway vs downstream complication), not "
        f"the broadest or first-mentioned symptom.\n"
        f"- When titles overlap, prefer the **more specific** title that still fits; use bucket "
        f"{n} only when none of the first {n - 1} titles is a reasonable primary fit.\n"
        f"- Amenorrhoea / oligomenorrhoea / fertility / pregnancy planning without a sellar "
        f"mass or visual-field defect: prefer Reproductive Endocrinology over Pituitary.\n"
        f"- Thyroid nodule, goitre, TSH/T4, thyroid cancer surveillance: prefer Thyroid Gland "
        f"over Reproductive unless the stem's answer hinges on gonadal hormones.\n"
        f"- Adrenal insufficiency, Cushing's, phaeochromocytoma, adrenal incidentaloma: "
        f"prefer Adrenal Glands over Pituitary when the adrenal axis is primary."
    )

    response = llm.complete(classification_prompt)
    raw = str(response).strip()

    digit = None
    for ch in raw:
        if ch.isdigit():
            digit = int(ch)
            break

    if digit is not None and 1 <= digit <= len(ROUTING_CATEGORIES):
        return ROUTING_CATEGORIES[digit - 1]
    print(f"⚠️  LLM returned unexpected classification '{raw}', falling back to 'Other'")
    return "Other"
