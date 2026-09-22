"""Interview preparation endpoints.

The API deliberately keeps the deterministic interview tools as the domain
implementation. This route layer owns the browser workflow: starting a
session, submitting one answer at a time, finishing early, and presenting
persisted history in a UI-friendly shape.
"""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

from fastapi import APIRouter, HTTPException

from openharness.jobhunt.api.deps import get_store
from openharness.jobhunt.api.schemas import (
    InterviewAnswerRequest,
    InterviewFinishRequest,
    InterviewPracticeRequest,
)
from openharness.jobhunt.storage import utc_now_iso
from openharness.tools.base import ToolExecutionContext
from openharness.tools.interview_tool import (
    InterviewFeedbackTool,
    InterviewFeedbackToolInput,
    InterviewPracticeTool,
    InterviewPracticeToolInput,
)

router = APIRouter(prefix="/interview", tags=["interview"])


def _context() -> ToolExecutionContext:
    store = get_store()
    return ToolExecutionContext(
        cwd=store.directory,
        metadata={"jobhunt_data_dir": str(store.directory)},
    )


def _find_session(session_id: str) -> tuple[Any, dict[str, Any]]:
    store = get_store()
    sessions = store.load_sessions()
    session = next((item for item in sessions if str(item.get("id")) == session_id), None)
    if session is None:
        raise HTTPException(status_code=404, detail="interview session not found")
    return store, session


def _grade(score: float) -> str:
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 55:
        return "一般"
    return "需加强"


def _summary(session: dict[str, Any]) -> dict[str, Any]:
    evaluations = [
        item for item in session.get("evaluations", []) if isinstance(item, dict)
    ]
    scores = [float(item.get("score", 0)) for item in evaluations]
    average_score = round(mean(scores), 1) if scores else 0.0
    question_count = len(session.get("questions") or [])
    answered_count = len(evaluations)
    status = str(session.get("status") or "进行中")
    return {
        "score": average_score,
        "grade": _grade(average_score) if scores else "未评分",
        "answered_count": answered_count,
        "question_count": question_count,
        "completion_rate": (
            round(answered_count / question_count * 100, 1) if question_count else 0.0
        ),
        "status": status,
        "evaluated_at": evaluations[-1].get("evaluated_at", "") if evaluations else "",
    }


def _session_view(
    session: dict[str, Any], *, include_answers: bool = True
) -> dict[str, Any]:
    view = dict(session)
    view["summary"] = _summary(session)
    questions = session.get("questions") or []
    current_index = int(session.get("current_index", len(session.get("evaluations") or [])))
    view["current_index"] = min(max(current_index, 0), len(questions))
    view["current_question"] = (
        questions[view["current_index"]]
        if view["status"] == "进行中" and view["current_index"] < len(questions)
        else None
    )
    if not include_answers:
        view["evaluations"] = [
            {key: value for key, value in item.items() if key != "answer"}
            for item in view.get("evaluations", [])
            if isinstance(item, dict)
        ]
    return view


def _save_session(store: Any, session: dict[str, Any]) -> None:
    sessions = store.load_sessions()
    for index, item in enumerate(sessions):
        if str(item.get("id")) == str(session.get("id")):
            sessions[index] = session
            store.save_sessions(sessions)
            return
    raise HTTPException(status_code=404, detail="interview session not found")


@router.post("/practice")
async def practice(request: InterviewPracticeRequest) -> dict[str, object]:
    """Create and persist a new guided interview session."""

    result = await InterviewPracticeTool().execute(
        InterviewPracticeToolInput(
            company=request.company,
            position=request.position,
            round=request.round,
            jd_text=request.jd_text,
            resume_text=request.resume_text,
            difficulty=request.difficulty,
            count=request.count,
        ),
        _context(),
    )
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.output)
    payload = json.loads(result.output)
    _store, session = _find_session(str(payload["session"]["id"]))
    return {
        **payload,
        "session": _session_view(session),
    }


@router.get("/sessions")
def list_sessions() -> dict[str, object]:
    """List recent sessions without returning full answer text."""

    items = [
        _session_view(session, include_answers=False)
        for session in get_store().load_sessions()
    ]
    return {"items": items, "total": len(items)}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, object]:
    """Return one session, including its answer history and current question."""

    _store, session = _find_session(session_id)
    return {"session": _session_view(session)}


@router.post("/sessions/{session_id}/answer")
async def submit_answer(
    session_id: str, request: InterviewAnswerRequest
) -> dict[str, object]:
    """Score the current answer and advance the session by one question."""

    store, session = _find_session(session_id)
    if session.get("status") != "进行中":
        raise HTTPException(status_code=409, detail="interview session is already finished")

    questions = session.get("questions") or []
    current_index = int(session.get("current_index", len(session.get("evaluations") or [])))
    if current_index >= len(questions):
        session["status"] = "已完成"
        session["completed_at"] = session.get("completed_at") or utc_now_iso()
        _save_session(store, session)
        raise HTTPException(status_code=409, detail="interview session is already finished")
    if request.question_index is not None and request.question_index != current_index:
        raise HTTPException(
            status_code=409,
            detail=f"stale question: expected index {current_index}",
        )

    question = questions[current_index]
    result = await InterviewFeedbackTool().execute(
        InterviewFeedbackToolInput(
            question=str(question.get("question", "")),
            answer=request.answer,
            position=str(session.get("position", "")),
            round=str(session.get("round", "技术")),
            session_id=session_id,
            question_index=current_index,
        ),
        _context(),
    )
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.output)

    feedback = json.loads(result.output)
    sessions = store.load_sessions()
    updated = next((item for item in sessions if str(item.get("id")) == session_id), None)
    if updated is None:
        raise HTTPException(status_code=404, detail="interview session not found")
    updated["current_index"] = current_index + 1
    updated["last_answered_at"] = utc_now_iso()
    if updated["current_index"] >= len(questions):
        updated["status"] = "已完成"
        updated["completed_at"] = utc_now_iso()
    store.save_sessions(sessions)

    view = _session_view(updated)
    return {
        "feedback": feedback,
        "session": view,
        "next_question": view.get("current_question"),
        "finished": view["status"] != "进行中",
    }


@router.post("/sessions/{session_id}/finish")
def finish_session(
    session_id: str, request: InterviewFinishRequest | None = None
) -> dict[str, object]:
    """Finish a session early or make a completed session reportable again."""

    store, session = _find_session(session_id)
    if session.get("status") == "进行中":
        session["status"] = "已完成"
        session["completed_at"] = utc_now_iso()
        if request and request.reason.strip():
            session["finish_reason"] = request.reason.strip()
        _save_session(store, session)
    return {"session": _session_view(session), "report": _summary(session)}
