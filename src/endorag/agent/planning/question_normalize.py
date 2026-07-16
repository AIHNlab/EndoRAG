from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NormalizedOption:
    letter: str
    text: str

QuestionFormat = Literal["block", "inline", "passthrough"]

# Block MCQ: uppercase A–E at line starts (Adrenal, Thyroid, etc.)
_BLOCK_OPTION_RE = re.compile(
    r"(?ims)"
    r"(?:^|\n)\s*"
    r"([A-E])"
    r"[\.\):]\s*"
    r"(.+?)"
    r"(?=(?:\n\s*[A-E][\.\):]\s*)|\Z)"
)

# Inline MCQ: lowercase a–e with closing paren (Diabetes, UKEU, etc.)
_INLINE_OPTION_RE = re.compile(
    r"(?i)"
    r"(?:^|[?,]\s*|\s+or\s+|\s+(?=[a-e]\)))"
    r"\s*"
    r"([a-e])"
    r"\)\s*"
    r"(.+?)"
    r"(?=(?:\s*,\s*[a-e]\)|\s+or\s+[a-e]\)|\s+[a-e]\)|$))"
)

# Canonical parser always sees this shape after normalization.
_CANONICAL_OPTION_RE = re.compile(
    r"(?ims)"
    r"(?:^|\n)\s*"
    r"([A-E])"
    r"\.\s*"
    r"(.+?)"
    r"(?=(?:\n\s*[A-E]\.\s*)|\Z)"
)


@dataclass(frozen=True)
class StandardizedQuestion:
    original: str
    canonical: str
    stem: str
    options: list[NormalizedOption]
    format: QuestionFormat


def standardize_exam_question(question: str) -> StandardizedQuestion:
    """Normalize exam input to a single canonical MCQ layout when possible."""
    original = str(question or "").strip()
    if not original:
        return StandardizedQuestion(
            original="",
            canonical="",
            stem="",
            options=[],
            format="passthrough",
        )

    block_options = _extract_block_options(original)
    if len(block_options) >= 2:
        stem = _stem_before_match(original, _BLOCK_OPTION_RE.search(original))
        canonical = _to_canonical(stem, block_options)
        return StandardizedQuestion(
            original=original,
            canonical=canonical,
            stem=stem,
            options=block_options,
            format="block",
        )

    inline_region = _inline_options_region(original)
    inline_options = _extract_inline_options(inline_region)
    if len(inline_options) >= 2:
        stem = _inline_stem(original, inline_region)
        canonical = _to_canonical(stem, inline_options)
        return StandardizedQuestion(
            original=original,
            canonical=canonical,
            stem=stem,
            options=inline_options,
            format="inline",
        )

    return StandardizedQuestion(
        original=original,
        canonical=original,
        stem=original,
        options=[],
        format="passthrough",
    )


def strip_canonical_options(question: str) -> str:
    """Return the stem from canonical or raw question text."""
    first = _CANONICAL_OPTION_RE.search(question)
    if first:
        return question[: first.start()].strip()
    first = _BLOCK_OPTION_RE.search(question)
    if first:
        return question[: first.start()].strip()
    return question.strip()


def _extract_block_options(question: str) -> list[NormalizedOption]:
    return _dedupe_option_letters(
        [
            NormalizedOption(letter=match.group(1).lower(), text=_clean_option_text(match.group(2)))
            for match in _BLOCK_OPTION_RE.finditer(question)
        ]
    )


def _extract_inline_options(text: str) -> list[NormalizedOption]:
    region = text.strip()
    return _dedupe_option_letters(
        [
            NormalizedOption(letter=match.group(1).lower(), text=_clean_option_text(match.group(2)))
            for match in _INLINE_OPTION_RE.finditer(region)
        ]
    )


def _inline_options_region(question: str) -> str:
    question_mark = question.rfind("?")
    if question_mark >= 0:
        return question[question_mark + 1 :]
    match = re.search(r"(?i)(?:^|[?,]\s*|\s+or\s+)[a-e]\)", question)
    return question[match.start() :] if match else question


def _inline_stem(question: str, inline_region: str) -> str:
    region_start = question.find(inline_region)
    if region_start > 0:
        return question[:region_start].strip()
    question_mark = question.rfind("?")
    if question_mark >= 0:
        return question[: question_mark + 1].strip()
    match = re.search(r"(?i)(?:^|[?,]\s*|\s+or\s+)[a-e]\)", question)
    return question[: match.start()].strip() if match else question.strip()


def _stem_before_match(question: str, match: re.Match[str] | None) -> str:
    if match:
        return question[: match.start()].strip()
    return question.strip()


def _to_canonical(stem: str, options: list[NormalizedOption]) -> str:
    stem_text = " ".join(stem.split())
    option_lines = [f"{option.letter.upper()}. {option.text}" for option in options]
    return "\n".join([stem_text, "", *option_lines])


def _clean_option_text(text: str) -> str:
    cleaned = " ".join(str(text).split())
    return cleaned.strip(" ,;")


def _dedupe_option_letters(options: list[NormalizedOption]) -> list[NormalizedOption]:
    seen: set[str] = set()
    unique: list[NormalizedOption] = []
    for option in options:
        if option.letter in seen:
            continue
        seen.add(option.letter)
        unique.append(option)
    return unique
