from __future__ import annotations


def rank_passages_for_question(
    passages: list[dict],
    *,
    anchor_query: str = "",
    priority_ids: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Order passages by answerability ids, anchor query, then reranker score."""
    if not passages:
        return []

    anchor_norm = " ".join(anchor_query.split()).lower() if anchor_query else ""
    priority = {str(item) for item in (priority_ids or []) if item}

    ranked = sorted(
        passages,
        key=lambda passage: (
            str(passage.get("id", "")) in priority,
            _matches_anchor_query(passage, anchor_norm),
            _passage_score(passage),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _passage_score(passage: dict) -> float:
    return float(
        passage.get("score")
        or (passage.get("metadata") or {}).get("retrieval_score")
        or 0.0
    )


def _matches_anchor_query(passage: dict, anchor_norm: str) -> bool:
    if not anchor_norm:
        return False
    query_norm = " ".join(str(passage.get("query", "")).split()).lower()
    return query_norm == anchor_norm
