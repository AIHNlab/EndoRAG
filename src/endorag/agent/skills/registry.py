from endorag.agent.skills.analyze_clinical_question import AnalyzeClinicalQuestionSkill
from endorag.agent.skills.analyze_mcq_stem import AnalyzeMCQStemSkill
from endorag.agent.skills.compose_guidance import ComposeGuidanceSkill
from endorag.agent.skills.judge_answerability import JudgeAnswerabilitySkill
from endorag.agent.skills.mark_invalid_question import MarkInvalidQuestionSkill
from endorag.agent.skills.reason_mcq_answer import ReasonMCQAnswerSkill
from endorag.agent.skills.retrieve_evidence import RetrieveEvidenceSkill


SKILL_REGISTRY = {
    "mark_invalid_question": MarkInvalidQuestionSkill(),
    "analyze_mcq_stem": AnalyzeMCQStemSkill(),
    "analyze_clinical_question": AnalyzeClinicalQuestionSkill(),
    "retrieve_evidence": RetrieveEvidenceSkill(),
    "judge_answerability": JudgeAnswerabilitySkill(),
    "reason_mcq_answer": ReasonMCQAnswerSkill(),
    "compose_guidance": ComposeGuidanceSkill(),
}
