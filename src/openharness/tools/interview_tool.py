"""Interview preparation tools: interview_questions / interview_practice / interview_feedback.

- ``interview_questions`` generates high-frequency questions for a JD:
  real questions from the RAG ``interview`` collection first (target company
  prioritised), then the deterministic bank keyed by the JD's skills.
- ``interview_practice`` starts a mock-interview session (question set plus
  pacing guidance) and persists it to ``interview_sessions.json`` under the
  job-hunt data directory.
- ``interview_feedback`` evaluates one answer with the rubric in
  :func:`openharness.jobhunt.scoring.evaluate_interview_answer` and can
  record the score back into a practice session.

The question bank and selection logic live in
:mod:`openharness.jobhunt.questions`; this module is the thin tool layer.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import extract_skills, parse_jd_text, parse_resume_text
from openharness.jobhunt.questions import (
    DIFFICULTIES,
    ROUND_GUIDANCE,
    ROUNDS,
    InterviewQuestion,
    build_questions,
)
from openharness.jobhunt.scoring import CandidateProfile, evaluate_interview_answer
from openharness.jobhunt.storage import JobHuntStore, new_record_id, utc_now_iso
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_DIFFICULTY_OPTIONS: tuple[str, ...] = (*DIFFICULTIES, "混合")

_EXCERPT_LIMIT = 320
_QUESTION_EXCERPT_LIMIT = 120


def _round_error(round_kind: str) -> str | None:
    """Return an error message for an unknown interview round."""
    if round_kind not in ROUNDS:
        return f"未知面试轮次 '{round_kind}'：可选 {' / '.join(ROUNDS)}"
    return None


def _difficulty_error(difficulty: str) -> str | None:
    """Return an error message for an unknown difficulty level."""
    if difficulty not in _DIFFICULTY_OPTIONS:
        return f"未知难度 '{difficulty}'：可选 {' / '.join(_DIFFICULTY_OPTIONS)}"
    return None


def _excerpt(text: str, limit: int) -> str:
    """Collapse whitespace (newlines included) and truncate for display."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _feedback_grade(score: float) -> str:
    """Map a 0-100 answer score to a coarse grade label."""
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 55:
        return "一般"
    return "需加强"


def _evaluation_payload(evaluation: Any) -> dict[str, Any]:
    return {
        "score": evaluation.score,
        "dimensions": [
            {
                "name": dimension.name,
                "score": dimension.score,
                "max_score": dimension.max_score,
                "advice": dimension.advice,
            }
            for dimension in evaluation.dimensions
        ],
        "missing_keywords": list(evaluation.missing_keywords),
        "suggestions": list(evaluation.suggestions),
    }


def _resolve_practice_skills(
    store: JobHuntStore, jd_text: str, resume_text: str, profile: dict[str, Any]
) -> tuple[list[str], str]:
    """Resolve practice-question skills: JD > resume > stored profile."""
    if jd_text.strip():
        jd = parse_jd_text(jd_text)
        skills = [*jd.required_skills, *jd.preferred_skills]
        if not skills:
            skills = extract_skills(jd_text)
        return skills, "jd"
    if resume_text.strip():
        return list(parse_resume_text(resume_text).technical_skills), "resume"
    skills = list(CandidateProfile.from_profile_dict(profile).skills) if profile else []
    return skills, "profile" if skills else ""


# ---------------------------------------------------------------------------
# interview_questions
# ---------------------------------------------------------------------------


class InterviewQuestionsToolInput(BaseModel):
    """Arguments for the interview_questions tool."""

    jd_text: str = Field(description="Target job description text")
    company: str = Field(
        default="",
        description="Target company; its RAG interview experiences are prioritised",
    )
    round: str = Field(default="技术", description="Interview round: 技术 / 行为 / HR / 高管")
    difficulty: str = Field(
        default="混合", description="Question difficulty: 基础 / 进阶 / 困难 / 混合"
    )
    count: int = Field(default=8, ge=1, le=20, description="Maximum generated questions")


class InterviewQuestionsTool(JobHuntToolBase):
    """Generate high-frequency interview questions for a job description."""

    name = "interview_questions"
    description = (
        "Generate high-frequency interview questions for a job description: real "
        "questions from the RAG 'interview' collection first (target company "
        "prioritised), then deterministic bank questions keyed by the JD's skills. "
        "Covers 八股文 / 算法 / 系统设计 / 项目 / 行为 / HR by round, each with an "
        "answer hint. Read-only."
    )
    input_model = InterviewQuestionsToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: InterviewQuestionsToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        if not arguments.jd_text.strip():
            return error_result("'jd_text' must be non-empty")
        round_error = _round_error(arguments.round)
        if round_error:
            return error_result(round_error)
        difficulty_error = _difficulty_error(arguments.difficulty)
        if difficulty_error:
            return error_result(difficulty_error)

        company = arguments.company.strip()
        jd = parse_jd_text(arguments.jd_text)
        skills = [*jd.required_skills, *jd.preferred_skills]
        if not skills:
            skills = extract_skills(arguments.jd_text)
        generated = build_questions(
            skills,
            round_kind=arguments.round,
            difficulty=arguments.difficulty,
            limit=arguments.count,
        )

        real: list[dict[str, Any]] = []
        notes: list[str] = []
        query = " ".join([company, jd.title, *skills[:6]]).strip() or "面试经验"
        where = {"company": company} if company else None
        try:
            retriever = self.resolve_retriever()
            outcome = await retriever.retrieve(
                query, collection="interview", where=where, top_n=5
            )
        #面经库不可用时仍返回题库生成的题目，不中断
        except Exception as exc:  # noqa: BLE001
            notes.append(f"面经库检索不可用：{exc}（可先导入面经文档后重试）")
        else:
            for hit in outcome.hits:
                real.append(
                    {
                        "doc_id": hit.id,
                        "company": str(hit.metadata.get("company", "")),
                        "excerpt": _excerpt(hit.text, _EXCERPT_LIMIT),
                        "score": round(float(hit.score), 4),
                    }
                )
            if not outcome.hits:
                if outcome.notes:
                    notes.extend(str(note) for note in outcome.notes)
                notes.append(
                    "面经库暂无相关题目：可导入面经文本后重试；"
                    "以下题目由 JD 技能与内置题库生成"
                )

        payload: dict[str, Any] = {
            "company": company,
            "round": arguments.round,
            "difficulty": arguments.difficulty,
            "jd_summary": {
                "title": jd.title,
                "required_skills": list(jd.required_skills),
                "preferred_skills": list(jd.preferred_skills),
            },
            "real_questions": real,
            "generated_questions": [_question_dict(q) for q in generated],
            "total": len(real) + len(generated),
        }
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"real_count": len(real), "generated_count": len(generated)},
        )


def _question_dict(question: InterviewQuestion) -> dict[str, str]:
    return question.to_dict()


# ---------------------------------------------------------------------------
# interview_practice
# ---------------------------------------------------------------------------


class InterviewPracticeToolInput(BaseModel):
    """Arguments for the interview_practice tool."""

    position: str = Field(description="Position / role being interviewed for")
    company: str = Field(default="", description="Target company (optional)")
    round: str = Field(default="技术", description="Interview round: 技术 / 行为 / HR / 高管")
    jd_text: str = Field(default="", description="JD text: questions follow its skills")
    resume_text: str = Field(
        default="", description="Resume text: personalises the project questions"
    )
    difficulty: str = Field(
        default="混合", description="Question difficulty: 基础 / 进阶 / 困难 / 混合"
    )
    count: int = Field(default=8, ge=1, le=20, description="Number of questions in the session")


class InterviewPracticeTool(JobHuntToolBase):
    """Start a mock-interview session and persist it locally."""

    name = "interview_practice"
    description = (
        "Start a mock interview for a position: builds a question set for the "
        "chosen round (技术 / 行为 / HR / 高管) from JD, resume or profile skills, "
        "with pacing guidance, and persists the session to the local job-hunt "
        "store for later scoring via interview_feedback. Writes locally."
    )
    input_model = InterviewPracticeToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    async def execute(
        self, arguments: InterviewPracticeToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        position = arguments.position.strip()
        if not position:
            return error_result("'position' 为必填")
        round_error = _round_error(arguments.round)
        if round_error:
            return error_result(round_error)
        difficulty_error = _difficulty_error(arguments.difficulty)
        if difficulty_error:
            return error_result(difficulty_error)

        store = self.resolve_store(context)
        profile = self.resolve_profile(context)
        skills, skill_source = _resolve_practice_skills(
            store, arguments.jd_text, arguments.resume_text, profile
        )
        questions = build_questions(
            skills,
            round_kind=arguments.round,
            difficulty=arguments.difficulty,
            limit=arguments.count,
        )
        now = utc_now_iso()
        session: dict[str, Any] = {
            "id": new_record_id("int"),
            "position": position,
            "company": arguments.company.strip(),
            "round": arguments.round,
            "difficulty": arguments.difficulty,
            "skill_source": skill_source,
            "created_at": now,
            "status": "进行中",
            "questions": [_question_dict(question) for question in questions],
            "evaluations": [],
        }
        sessions = store.load_sessions()
        sessions.insert(0, session)
        store.save_sessions(sessions)

        payload: dict[str, Any] = {
            "session": {
                "id": session["id"],
                "position": position,
                "company": session["company"],
                "round": arguments.round,
                "created_at": now,
                "question_count": len(questions),
                "total_sessions": len(sessions),
            },
            "guidance": ROUND_GUIDANCE[arguments.round],
            "questions": session["questions"],
            "next_steps": [
                "逐题口头回答后调用 interview_feedback（携带 session_id 与 question/answer）记录评分与改进建议",
                "面试结束后可用 application_update 更新对应投递的状态",
            ],
        }
        if not skills:
            payload["notes"] = [
                (
                    "未提供 jd_text / resume_text 且本地画像为空：题目来自通用题库；"
                    "补充 JD 或先调用 profile_update 可获得更贴合的题目"
                )
            ]
        return ToolResult(
            output=json_output(payload),
            metadata={"session_id": session["id"], "question_count": len(questions)},
        )


# ---------------------------------------------------------------------------
# interview_feedback
# ---------------------------------------------------------------------------


class InterviewFeedbackToolInput(BaseModel):
    """Arguments for the interview_feedback tool."""

    question: str = Field(description="The interview question that was answered")
    answer: str = Field(description="The candidate's answer text")
    position: str = Field(default="", description="Position being interviewed for (context)")
    round: str = Field(default="技术", description="Interview round: 技术 / 行为 / HR / 高管")
    session_id: str = Field(
        default="",
        description="Optional practice session id: the evaluation is recorded into it",
    )


class InterviewFeedbackTool(JobHuntToolBase):
    """Evaluate one interview answer with the deterministic rubric."""

    name = "interview_feedback"
    description = (
        "Evaluate one interview answer with a documented rubric (content coverage "
        "40 / structure 30 / quantified detail 20 / expression 10) and return "
        "per-dimension scores, missing keywords and concrete improvement advice. "
        "Pass session_id from interview_practice to record the score into that "
        "session. Writes only when session_id is given."
    )
    input_model = InterviewFeedbackToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        session_id = getattr(arguments, "session_id", "")
        return not str(session_id).strip()

    async def execute(
        self, arguments: InterviewFeedbackToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        question = arguments.question.strip()
        answer = arguments.answer.strip()
        if not question:
            return error_result("'question' 为必填")
        if not answer:
            return error_result("'answer' 为必填：请提供你的回答文本")
        round_error = _round_error(arguments.round)
        if round_error:
            return error_result(round_error)

        round_kind = "行为" if arguments.round == "行为" else "技术"
        evaluation = evaluate_interview_answer(question, answer, round_kind=round_kind)
        grade = _feedback_grade(evaluation.score)
        payload: dict[str, Any] = {
            "position": arguments.position.strip(),
            "round": arguments.round,
            **_evaluation_payload(evaluation),
            "grade": grade,
        }

        session_id = arguments.session_id.strip()
        if session_id:
            store = self.resolve_store(context)
            sessions = store.load_sessions()
            target = next(
                (item for item in sessions if item.get("id") == session_id), None
            )
            if target is None:
                payload["session"] = {
                    "id": session_id,
                    "recorded": False,
                    "note": "未找到该练习会话：评分未入库（可先用 interview_practice 创建）",
                }
            else:
                evaluations = target.setdefault("evaluations", [])
                evaluations.append(
                    {
                        "question": _excerpt(question, _QUESTION_EXCERPT_LIMIT),
                        "score": evaluation.score,
                        "grade": grade,
                        "evaluated_at": utc_now_iso(),
                    }
                )
                store.save_sessions(sessions)
                payload["session"] = {
                    "id": session_id,
                    "recorded": True,
                    "evaluation_count": len(evaluations),
                }

        return ToolResult(
            output=json_output(payload),
            metadata={"score": evaluation.score, "grade": grade},
        )
