"""Tests for the interview tools (interview_questions / practice / feedback)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.jobhunt.questions import ROUND_GUIDANCE
from openharness.jobhunt.storage import JobHuntStore
from openharness.tools.base import ToolResult
from openharness.tools.interview_tool import (
    InterviewFeedbackTool,
    InterviewFeedbackToolInput,
    InterviewPracticeTool,
    InterviewPracticeToolInput,
    InterviewQuestionsTool,
    InterviewQuestionsToolInput,
)
from openharness.tools.user_profile_tool import ProfileUpdateTool, ProfileUpdateToolInput

GIL_QUESTION = "什么是 GIL？它如何影响 Python 的多线程性能？"

STRONG_ANSWER = (
    "首先，GIL 是 CPython 的全局解释器锁：同一时刻只有一个线程能执行 Python 字节码，"
    "所以多线程无法利用多核做并行计算，CPU 密集型任务的性能提升很有限。"
    "然后，我的做法是把 CPU 密集模块改用多进程，接口耗时从 2s 降低到 200ms，吞吐提升大约 3 倍。"
    "最后做个总结：IO 密集型保留多线程仍然有效，因为等待 IO 时 GIL 会释放；"
    "如果必须多核并行，就换成多进程或 C 扩展。"
)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


# ---------------------------------------------------------------------------
# interview_questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_questions_generated_without_experiences(
    jd_text, settings, ctx, make_retriever
) -> None:
    result = await InterviewQuestionsTool(
        retriever=make_retriever(), settings=settings
    ).execute(InterviewQuestionsToolInput(jd_text=jd_text), ctx)

    assert result.is_error is False
    payload = _payload(result)
    assert payload["round"] == "技术"
    assert payload["difficulty"] == "混合"
    assert payload["jd_summary"] == {
        "title": "Python 后端开发工程师",
        "required_skills": ["Python", "FastAPI", "MySQL", "Redis"],
        "preferred_skills": ["Docker", "Kubernetes"],
    }
    assert payload["real_questions"] == []

    questions = payload["generated_questions"]
    assert len(questions) == 8
    assert payload["total"] == 8
    assert any("GIL" in question["question"] for question in questions)
    assert {question["category"] for question in questions} == {
        "八股文",
        "算法",
        "系统设计",
        "项目",
    }
    assert all(question["hint"] for question in questions)
    assert any("面经库暂无相关题目" in note for note in payload["notes"])
    assert result.metadata == {"real_count": 0, "generated_count": 8}


@pytest.mark.asyncio
async def test_interview_questions_prioritises_company_experiences(
    jd_text, settings, ctx, make_hit, make_retriever
) -> None:
    experience = "面经：一面先深挖项目，然后问了 GIL 和 MySQL 索引优化。"
    retriever = make_retriever(
        hits={
            "interview": [
                make_hit(
                    experience,
                    doc_id="exp-1",
                    collection="interview",
                    company="杭州星辰科技有限公司",
                    score=0.87,
                )
            ]
        }
    )

    result = await InterviewQuestionsTool(retriever=retriever, settings=settings).execute(
        InterviewQuestionsToolInput(jd_text=jd_text, company="杭州星辰科技有限公司"), ctx
    )

    payload = _payload(result)
    assert len(payload["real_questions"]) == 1
    real = payload["real_questions"][0]
    assert real["doc_id"] == "exp-1"
    assert real["company"] == "杭州星辰科技有限公司"
    assert real["excerpt"] == experience
    assert real["score"] == 0.87
    assert payload["total"] == 9
    assert "notes" not in payload
    assert result.metadata == {"real_count": 1, "generated_count": 8}
    # 携带目标公司作为检索过滤条件
    assert retriever.calls[0]["where"] == {"company": "杭州星辰科技有限公司"}
    assert retriever.calls[0]["collections"] == ["interview"]


@pytest.mark.asyncio
async def test_interview_questions_round_and_difficulty_control(
    jd_text, settings, ctx, make_retriever
) -> None:
    hr = await InterviewQuestionsTool(
        retriever=make_retriever(), settings=settings
    ).execute(InterviewQuestionsToolInput(jd_text=jd_text, round="HR"), ctx)

    payload = _payload(hr)
    assert len(payload["generated_questions"]) == 8
    assert {question["category"] for question in payload["generated_questions"]} == {
        "HR",
        "行为",
    }

    hard = await InterviewQuestionsTool(
        retriever=make_retriever(), settings=settings
    ).execute(
        InterviewQuestionsToolInput(jd_text=jd_text, difficulty="困难", count=6), ctx
    )

    payload = _payload(hard)
    assert len(payload["generated_questions"]) == 6
    assert all(
        question["difficulty"] != "基础" for question in payload["generated_questions"]
    )


@pytest.mark.asyncio
async def test_interview_questions_validation_errors(jd_text, settings, ctx) -> None:
    tool = InterviewQuestionsTool(settings=settings)

    empty = await tool.execute(InterviewQuestionsToolInput(jd_text="  "), ctx)
    assert empty.is_error is True
    assert "'jd_text' must be non-empty" in empty.output

    bad_round = await tool.execute(
        InterviewQuestionsToolInput(jd_text=jd_text, round="奇葩"), ctx
    )
    assert bad_round.is_error is True
    assert "未知面试轮次 '奇葩'" in bad_round.output

    bad_difficulty = await tool.execute(
        InterviewQuestionsToolInput(jd_text=jd_text, difficulty="地狱"), ctx
    )
    assert bad_difficulty.is_error is True
    assert "未知难度 '地狱'" in bad_difficulty.output


@pytest.mark.asyncio
async def test_interview_questions_offline_still_generates(
    jd_text, settings, ctx, offline_retriever
) -> None:
    result = await InterviewQuestionsTool(
        retriever=offline_retriever, settings=settings
    ).execute(InterviewQuestionsToolInput(jd_text=jd_text), ctx)

    assert result.is_error is False
    payload = _payload(result)
    assert payload["real_questions"] == []
    assert len(payload["generated_questions"]) == 8
    assert "面经库检索不可用" in payload["notes"][0]
    assert "rag offline (test)" in payload["notes"][0]
    assert result.metadata == {"real_count": 0, "generated_count": 8}


# ---------------------------------------------------------------------------
# interview_practice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_practice_creates_persisted_session(
    jd_text, settings, ctx
) -> None:
    result = await InterviewPracticeTool(settings=settings).execute(
        InterviewPracticeToolInput(
            position="Python 后端开发工程师",
            company="杭州星辰科技有限公司",
            jd_text=jd_text,
        ),
        ctx,
    )

    assert result.is_error is False
    payload = _payload(result)
    session = payload["session"]
    assert session["id"].startswith("int-")
    assert session["position"] == "Python 后端开发工程师"
    assert session["company"] == "杭州星辰科技有限公司"
    assert session["round"] == "技术"
    assert session["question_count"] == 8
    assert session["total_sessions"] == 1
    assert payload["guidance"] == ROUND_GUIDANCE["技术"]
    assert len(payload["questions"]) == 8
    assert len(payload["next_steps"]) == 2
    assert "notes" not in payload
    assert result.metadata == {"session_id": session["id"], "question_count": 8}

    sessions = JobHuntStore(ctx.cwd).load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == session["id"]
    assert sessions[0]["skill_source"] == "jd"
    assert sessions[0]["status"] == "进行中"
    assert sessions[0]["evaluations"] == []


@pytest.mark.asyncio
async def test_interview_practice_skill_source_precedence(
    resume_text, profile_dict, settings, ctx
) -> None:
    await ProfileUpdateTool(settings=settings).execute(
        ProfileUpdateToolInput(updates=profile_dict), ctx
    )
    tool = InterviewPracticeTool(settings=settings)

    from_resume = await tool.execute(
        InterviewPracticeToolInput(position="后端工程师", resume_text=resume_text), ctx
    )
    payload = _payload(from_resume)
    assert "notes" not in payload
    sessions = JobHuntStore(ctx.cwd).load_sessions()
    assert sessions[0]["skill_source"] == "resume"

    from_profile = await tool.execute(
        InterviewPracticeToolInput(position="后端工程师"), ctx
    )
    payload = _payload(from_profile)
    assert "notes" not in payload
    sessions = JobHuntStore(ctx.cwd).load_sessions()
    assert sessions[0]["skill_source"] == "profile"

    # 第二次创建把会话插到最前
    assert payload["session"]["total_sessions"] == 2


@pytest.mark.asyncio
async def test_interview_practice_generic_bank_without_sources(settings, ctx) -> None:
    result = await InterviewPracticeTool(settings=settings).execute(
        InterviewPracticeToolInput(position="后端工程师"), ctx
    )

    payload = _payload(result)
    sessions = JobHuntStore(ctx.cwd).load_sessions()
    assert sessions[0]["skill_source"] == ""
    assert payload["questions"]
    assert payload["session"]["question_count"] == len(payload["questions"])
    assert "未提供 jd_text / resume_text 且本地画像为空" in payload["notes"][0]


@pytest.mark.asyncio
async def test_interview_practice_validation_errors(settings, ctx) -> None:
    tool = InterviewPracticeTool(settings=settings)

    empty = await tool.execute(InterviewPracticeToolInput(position="  "), ctx)
    assert empty.is_error is True
    assert "'position' 为必填" in empty.output

    bad_round = await tool.execute(
        InterviewPracticeToolInput(position="后端", round="群面"), ctx
    )
    assert bad_round.is_error is True
    assert "未知面试轮次 '群面'" in bad_round.output

    bad_difficulty = await tool.execute(
        InterviewPracticeToolInput(position="后端", difficulty="地狱"), ctx
    )
    assert bad_difficulty.is_error is True
    assert "未知难度 '地狱'" in bad_difficulty.output


# ---------------------------------------------------------------------------
# interview_feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interview_feedback_records_into_session(jd_text, settings, ctx) -> None:
    practice = await InterviewPracticeTool(settings=settings).execute(
        InterviewPracticeToolInput(position="Python 后端开发工程师", jd_text=jd_text), ctx
    )
    session_id = _payload(practice)["session"]["id"]

    result = await InterviewFeedbackTool(settings=settings).execute(
        InterviewFeedbackToolInput(
            question=GIL_QUESTION, answer=STRONG_ANSWER, session_id=session_id
        ),
        ctx,
    )

    payload = _payload(result)
    assert payload["score"] == 94.0
    assert payload["grade"] == "优秀"
    assert payload["round"] == "技术"
    assert payload["session"] == {
        "id": session_id,
        "recorded": True,
        "evaluation_count": 1,
    }
    assert result.metadata == {"score": 94.0, "grade": "优秀"}

    # 再次评分：计数累加并持久化到会话
    await InterviewFeedbackTool(settings=settings).execute(
        InterviewFeedbackToolInput(
            question="谈谈 Python 的内存管理与垃圾回收机制",
            answer=STRONG_ANSWER,
            session_id=session_id,
        ),
        ctx,
    )

    sessions = JobHuntStore(ctx.cwd).load_sessions()
    assert len(sessions[0]["evaluations"]) == 2
    assert sessions[0]["evaluations"][0]["question"] == GIL_QUESTION
    assert sessions[0]["evaluations"][0]["score"] == 94.0
    assert sessions[0]["evaluations"][0]["grade"] == "优秀"


@pytest.mark.asyncio
async def test_interview_feedback_readonly_without_session(settings, ctx) -> None:
    tool = InterviewFeedbackTool(settings=settings)

    result = await tool.execute(
        InterviewFeedbackToolInput(question=GIL_QUESTION, answer="不知道"), ctx
    )

    payload = _payload(result)
    assert result.is_error is False
    assert payload["score"] == 2.0
    assert payload["grade"] == "需加强"
    assert "session" not in payload
    assert "Python" in payload["missing_keywords"]
    assert payload["suggestions"]
    assert result.metadata == {"score": 2.0, "grade": "需加强"}

    assert tool.is_read_only(InterviewFeedbackToolInput(question="Q", answer="A")) is True
    assert (
        tool.is_read_only(
            InterviewFeedbackToolInput(question="Q", answer="A", session_id="int-1")
        )
        is False
    )


@pytest.mark.asyncio
async def test_interview_feedback_unknown_session_note(settings, ctx) -> None:
    result = await InterviewFeedbackTool(settings=settings).execute(
        InterviewFeedbackToolInput(
            question=GIL_QUESTION, answer="不知道", session_id="int-missing"
        ),
        ctx,
    )

    payload = _payload(result)
    assert payload["score"] == 2.0
    assert payload["session"]["recorded"] is False
    assert "未找到该练习会话" in payload["session"]["note"]


@pytest.mark.asyncio
async def test_interview_feedback_validation_errors(settings, ctx) -> None:
    tool = InterviewFeedbackTool(settings=settings)

    empty_question = await tool.execute(
        InterviewFeedbackToolInput(question="  ", answer="A"), ctx
    )
    assert empty_question.is_error is True
    assert "'question' 为必填" in empty_question.output

    empty_answer = await tool.execute(
        InterviewFeedbackToolInput(question="Q", answer="  "), ctx
    )
    assert empty_answer.is_error is True
    assert "'answer' 为必填" in empty_answer.output

    bad_round = await tool.execute(
        InterviewFeedbackToolInput(question="Q", answer="A", round="群面"), ctx
    )
    assert bad_round.is_error is True
    assert "未知面试轮次 '群面'" in bad_round.output


def test_interview_read_only_flags() -> None:
    assert (
        InterviewQuestionsTool().is_read_only(InterviewQuestionsToolInput(jd_text="x"))
        is True
    )
    assert (
        InterviewPracticeTool().is_read_only(InterviewPracticeToolInput(position="x"))
        is False
    )
