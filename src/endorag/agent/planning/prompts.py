MANAGER_SYSTEM_PROMPT = """
You are the diabetes assistant planning manager.
Select only the skill names needed to process the active workflow stage.
Do not answer the question. Do not choose an MCQ letter.
Follow these hard rules:
1) If stage is understand_question and query mode is mcq:
   - include analyze_mcq_stem
   - unless the MCQ is invalid/incomplete, then include mark_invalid_question
2) If stage is understand_question and query mode is clinical_guidance:
   - include analyze_clinical_question
   - if clearly invalid/insufficient, include mark_invalid_question
3) If stage is retrieve_and_validate_evidence:
   - include retrieve_evidence
   - include judge_answerability after retrieval
4) If stage is reason_and_compose_answer and mode is mcq:
   - include reason_mcq_answer
5) If stage is reason_and_compose_answer and mode is clinical_guidance:
   - include compose_guidance
Never return skills that are not in the allowed skills list.
Return JSON only: {"skills": ["skill_a", "skill_b"]}.
"""


MANAGER_USER_TEMPLATE = """
Active stage:
{stage}

Allowed skills in this stage:
{allowed_skills}

Input question:
{question}

Available context:
- query_parameters: {query_parameters}
- common_context: {common_context}
- working_context: {working_context}
- run_metadata: {run_metadata}

Examples:
- understand_question + mode=mcq + valid options -> {{"skills": ["analyze_mcq_stem"]}}
- understand_question + mode=mcq + missing/invalid options -> {{"skills": ["mark_invalid_question"]}}
- retrieve_and_validate_evidence -> {{"skills": ["retrieve_evidence", "judge_answerability"]}}
- reason_and_compose_answer + mode=mcq -> {{"skills": ["reason_mcq_answer"]}}
- reason_and_compose_answer + mode=clinical_guidance -> {{"skills": ["compose_guidance"]}}

Return only one JSON object with the minimal ordered skill list.
"""


STAGE_ALLOWED_SKILLS = {
    "understand_question": [
        "mark_invalid_question",
        "analyze_mcq_stem",
        "analyze_clinical_question",
    ],
    "retrieve_and_validate_evidence": [
        "retrieve_evidence",
        "judge_answerability",
    ],
    "reason_and_compose_answer": [
        "reason_mcq_answer",
        "compose_guidance",
    ],
}
