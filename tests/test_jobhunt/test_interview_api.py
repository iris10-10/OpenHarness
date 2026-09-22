"""Integration coverage for the browser interview workflow."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from openharness.jobhunt.api.app import create_app


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_JOBHUNT_DIR", str(tmp_path / "jobhunt"))
    return TestClient(create_app(static_dir=tmp_path / "missing-dist"))


def test_interview_web_workflow_persists_and_scores_session(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)

    started = client.post(
        "/api/interview/practice",
        json={
            "company": "星辰科技",
            "position": "Python 后端工程师",
            "round": "技术",
            "difficulty": "混合",
            "count": 2,
            "jd_text": "Python FastAPI MySQL Redis 系统设计",
        },
    )
    assert started.status_code == 200
    session = started.json()["session"]
    session_id = session["id"]
    assert session["status"] == "进行中"
    assert session["current_index"] == 0
    assert session["summary"]["completion_rate"] == 0.0

    first_question = session["current_question"]["question"]
    answered = client.post(
        f"/api/interview/sessions/{session_id}/answer",
        json={
            "question_index": 0,
            "answer": f"结论是：{first_question}。首先说明原理，然后给出结果提升 30%。",
        },
    )
    assert answered.status_code == 200
    answer_payload = answered.json()
    assert answer_payload["feedback"]["score"] > 0
    assert answer_payload["session"]["current_index"] == 1
    assert answer_payload["finished"] is False
    assert answer_payload["next_question"]["question"]

    stale = client.post(
        f"/api/interview/sessions/{session_id}/answer",
        json={"question_index": 0, "answer": "重复提交"},
    )
    assert stale.status_code == 409

    second_question = answer_payload["next_question"]["question"]
    finished = client.post(
        f"/api/interview/sessions/{session_id}/answer",
        json={
            "question_index": 1,
            "answer": f"背景是项目问题，行动是优化方案，最终延迟降低 40%。{second_question}",
        },
    )
    assert finished.status_code == 200
    assert finished.json()["finished"] is True
    assert finished.json()["session"]["status"] == "已完成"
    assert finished.json()["session"]["summary"]["answered_count"] == 2

    listed = client.get("/api/interview/sessions")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert "answer" not in listed.json()["items"][0]["evaluations"][0]

    detail = client.get(f"/api/interview/sessions/{session_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()["session"]
    assert detail_payload["summary"]["grade"] in {"优秀", "良好", "一般", "需加强"}
    assert detail_payload["evaluations"][0]["answer"]

    raw = json.loads(
        (tmp_path / "jobhunt" / "interview_sessions.json").read_text(encoding="utf-8")
    )
    assert raw[0]["status"] == "已完成"
    assert raw[0]["evaluations"][0]["question_index"] == 0


def test_interview_web_workflow_can_finish_early(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)

    started = client.post(
        "/api/interview/practice",
        json={"position": "后端工程师", "count": 3},
    )
    assert started.status_code == 200
    session_id = started.json()["session"]["id"]

    ended = client.post(
        f"/api/interview/sessions/{session_id}/finish",
        json={"reason": "时间不足"},
    )
    assert ended.status_code == 200
    assert ended.json()["session"]["status"] == "已完成"
    assert ended.json()["session"]["finish_reason"] == "时间不足"

    answer = client.post(
        f"/api/interview/sessions/{session_id}/answer",
        json={"answer": "不能继续回答"},
    )
    assert answer.status_code == 409
