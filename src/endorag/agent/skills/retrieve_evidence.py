from __future__ import annotations

from pydantic import BaseModel, Field

from endorag.retrieval.evidence_models import EvidencePassage
from endorag.retrieval.evidence_ranking import rank_passages_for_question
from endorag.retrieval.query_variants import (
    build_anchor_query,
    build_followup_query_variants,
)
from endorag.agent.planning.parameters import QueryParameters
from endorag.agent.skills.base import SkillContext, SkillResult, StatePatch


class RetrieveEvidenceInput(BaseModel):
    domain: str | None = None
    query_seed: str = ""
    retrieval_queries: list[str] = Field(default_factory=list)
    missing_anchors: list[str] = Field(default_factory=list)
    closest_competing_options: list[str] = Field(default_factory=list)
    supplemental_queries: list[str] = Field(default_factory=list)
    anchor_only: bool = False
    stem_type: str = "other"
    max_rounds: int | None = None
    top_k: int | None = None
    dependency_results: dict = Field(default_factory=dict)


class RetrieveEvidenceSkill:
    name = "retrieve_evidence"
    description = "Retrieve source passages from the diabetes knowledge base."
    input_model = RetrieveEvidenceInput

    async def run(
        self,
        task_id: str,
        inputs: RetrieveEvidenceInput,
        context: SkillContext,
        deps,
    ) -> SkillResult:
        if getattr(deps, "vector_tools", None) is None:
            return SkillResult(
                task_id=task_id,
                skill_name=self.name,
                status="failed",
                summary="Vector retrieval is not configured.",
                limitations=["deps.vector_tools is missing."],
            )

        params = QueryParameters.model_validate(context.query_parameters or {})
        max_rounds = inputs.max_rounds or getattr(getattr(deps, "settings", None), "max_retrieval_rounds", 2)
        attempted_queries: list[str] = []
        all_passages: list[EvidencePassage] = []
        provenance: list[dict] = []

        full_question = build_anchor_query(context.run_metadata.get("question", ""))
        if inputs.missing_anchors or inputs.supplemental_queries or inputs.closest_competing_options:
            queries = build_followup_query_variants(
                params,
                inputs.missing_anchors,
                inputs.supplemental_queries,
                inputs.closest_competing_options,
            )
        elif inputs.anchor_only:
            queries = [full_question] if full_question else []
        elif inputs.retrieval_queries:
            queries = _with_full_question_anchor(full_question, inputs.retrieval_queries)
        else:
            queries = [full_question] if full_question else []
        queries = _dedupe_queries(queries)

        for retrieval_round in range(1, max_rounds + 1):
            for query in queries:
                retrieval_domain = (
                    getattr(deps, "routing_domain", None)
                    or inputs.domain
                    or params.suspected_domain
                )
                passages, call_provenance = await deps.vector_tools.retrieve(
                    query=query,
                    domain=retrieval_domain,
                    top_k=inputs.top_k,
                    retrieval_round=retrieval_round,
                )
                attempted_queries.append(query)
                provenance.append(call_provenance)
                if hasattr(deps, "log_tool_call"):
                    deps.log_tool_call(call_provenance)
                all_passages.extend(passages)
            all_passages = _dedupe_passages(all_passages)
            break

        evidence_limit = 5 if inputs.anchor_only else 8
        evidence = rank_passages_for_question(
            [passage.model_dump() for passage in all_passages],
            anchor_query=full_question,
            limit=evidence_limit,
        )
        status = "success" if all_passages else "partial"
        limitations = [] if all_passages else ["No passages were retrieved from the configured vector store."]
        return SkillResult(
            task_id=task_id,
            skill_name=self.name,
            status=status,
            summary=f"Retrieved {len(all_passages)} unique passage(s) from {len(attempted_queries)} query(s).",
            data={
                "passages": evidence,
                "attempted_queries": attempted_queries,
                "domain": getattr(deps, "routing_domain", None) or inputs.domain or params.suspected_domain,
            },
            evidence=evidence,
            limitations=limitations,
            provenance=provenance,
            state_patches=[
                StatePatch(op="set", key="retrieval.passages", value=evidence, source=self.name)
            ],
            context_updates=[
                f"Retrieved {len(all_passages)} unique evidence passages.",
                f"Attempted queries: {attempted_queries[:5]}",
            ],
        )


def _with_full_question_anchor(full_question: str, queries: list[str]) -> list[str]:
    variants = []
    if full_question:
        variants.append(full_question)
    variants.extend(queries)
    return variants


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


def _dedupe_passages(passages: list[EvidencePassage]) -> list[EvidencePassage]:
    seen: set[str] = set()
    unique: list[EvidencePassage] = []
    for passage in passages:
        key = " ".join(passage.text.lower().split())[:1000]
        source_key = f"{passage.source}:{key}"
        if source_key not in seen:
            seen.add(source_key)
            unique.append(passage)
    return unique
