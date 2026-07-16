from endorag.agent.planning.parameters import QueryParameters


def build_anchor_query(question: str) -> str:
    return " ".join(str(question).split())[:2000]


def build_followup_query_variants(
    params: QueryParameters,
    missing_anchors: list[str],
    supplemental_queries: list[str] | None = None,
    competing_options: list[str] | None = None,
) -> list[str]:
    """Build follow-up queries from LLM-provided anchors and stem search phrases."""
    variants: list[str] = []
    for anchor in missing_anchors[:6]:
        anchor_text = " ".join(str(anchor).split())
        if not anchor_text:
            continue
        if params.refined_query_seed:
            variants.append(f"{params.refined_query_seed} {anchor_text}".strip())
        else:
            variants.append(anchor_text)
    variants.extend(_option_discriminative_queries(params, competing_options or []))
    if supplemental_queries:
        variants.extend(supplemental_queries[:3])
    return _dedupe_queries(variants)[:8]


def _option_discriminative_queries(
    params: QueryParameters,
    competing_options: list[str],
) -> list[str]:
    option_text_by_letter = {option.letter.lower(): option.text for option in params.options}
    competitors: list[str] = []
    for item in competing_options[:4]:
        normalized = " ".join(str(item).split())
        if not normalized:
            continue
        letter = normalized[:1].lower()
        if letter in option_text_by_letter:
            competitors.append(f"{letter}. {option_text_by_letter[letter]}")
        else:
            competitors.append(normalized[:240])

    if len(competitors) < 2:
        return []

    seed = params.working_context_seed or params.refined_query_seed
    seed = " ".join(seed.split())[:500]
    queries = []
    for idx, left in enumerate(competitors):
        for right in competitors[idx + 1 :]:
            queries.append(f"{seed} distinguish {left} versus {right}".strip())
    return queries[:4]


def _dedupe_queries(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    clean: list[str] = []
    for query in queries:
        normalized = " ".join(str(query).split())
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            clean.append(normalized)
    return clean
