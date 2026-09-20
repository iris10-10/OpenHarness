"""End-to-end job-hunt workflow tests without network or model credentials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.config.settings import Settings
from openharness.jobhunt.storage import JobHuntStore
from openharness.rag.sources.local_importer import LocalKnowledgeImporter
from openharness.tools.application_tracker_tool import (
    ApplicationCreateTool,
    ApplicationCreateToolInput,
    ApplicationListTool,
    ApplicationListToolInput,
    ApplicationUpdateTool,
    ApplicationUpdateToolInput,
)
from openharness.tools.base import ToolExecutionContext
from openharness.tools.interview_tool import (
    InterviewPracticeTool,
    InterviewPracticeToolInput,
)
from openharness.tools.job_match_tool import JobMatchTool, JobMatchToolInput
from openharness.tools.resume_tool import ResumeParseTool, ResumeParseToolInput

SAMPLE_DIR = Path(__file__).parents[2] / "examples" / "sample_data"


def _context(jobhunt_dir: Path) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=jobhunt_dir, metadata={"jobhunt_data_dir": str(jobhunt_dir)})


@pytest.mark.asyncio
async def test_resume_job_match_interview_and_application_flow(
    isolated_jobhunt: dict[str, Path | Settings],
    offline_retriever,
) -> None:
    """Exercise the primary user path from imported resume to tracked offer."""

    jobhunt_dir = isolated_jobhunt["jobhunt_dir"]
    assert isinstance(jobhunt_dir, Path)
    context = _context(jobhunt_dir)
    resume_text = (SAMPLE_DIR / "resume.md").read_text(encoding="utf-8")
    jd_path = SAMPLE_DIR / "jobs" / "python-backend.md"
    interview_path = SAMPLE_DIR / "interviews" / "python-backend.md"

    resume_result = await ResumeParseTool(
        retriever=offline_retriever,
        settings=isolated_jobhunt["settings"],
    ).execute(
        ResumeParseToolInput(text=resume_text, store_to_rag=True),
        context,
    )
    assert not resume_result.is_error
    resume_payload = json.loads(resume_result.output)
    assert resume_payload["storage"]["stored"] is True
    assert resume_payload["ats"]["total"] >= 60

    importer = LocalKnowledgeImporter(offline_retriever.store)
    jobs_report = importer.import_file(
        jd_path,
        collection="jobs",
        metadata={"kind": "job", "source_type": "sample"},
    )
    interview_report = importer.import_file(
        interview_path,
        collection="interview",
        metadata={"kind": "interview", "source_type": "sample"},
    )
    assert jobs_report.chunks_created >= 1
    assert interview_report.chunks_created >= 1

    match_result = await JobMatchTool(
        retriever=offline_retriever,
        settings=isolated_jobhunt["settings"],
    ).execute(
        JobMatchToolInput(resume_text=resume_text, query="Python 后端 FastAPI", top_k=3),
        context,
    )
    assert not match_result.is_error
    match_payload = json.loads(match_result.output)
    assert match_payload["matches"]
    best_match = match_payload["matches"][0]
    assert best_match["job"]["title"] == "Python 后端工程师"
    assert best_match["score"] >= 65
    assert isinstance(best_match["missing_skills"], list)
    assert best_match["covered_skills"]

    interview_result = await InterviewPracticeTool(
        retriever=offline_retriever,
        settings=isolated_jobhunt["settings"],
    ).execute(
        InterviewPracticeToolInput(
            position="Python 后端工程师",
            company="云帆科技",
            jd_text=jd_path.read_text(encoding="utf-8"),
            resume_text=resume_text,
            count=4,
        ),
        context,
    )
    assert not interview_result.is_error
    interview_payload = json.loads(interview_result.output)
    assert interview_payload["session"]["id"]
    assert len(interview_payload["questions"]) == 4
    assert interview_payload["questions"][0]["question"]
    assert JobHuntStore(jobhunt_dir).load_sessions()

    created_result = await ApplicationCreateTool().execute(
        ApplicationCreateToolInput(
            company="云帆科技",
            position="Python 后端工程师",
            channel="内推",
            notes="已完成技术面准备",
        ),
        context,
    )
    assert not created_result.is_error
    application_id = json.loads(created_result.output)["created"]["id"]

    updated_result = await ApplicationUpdateTool().execute(
        ApplicationUpdateToolInput(
            application_id=application_id,
            status="一面",
            note="约定下周进行技术面",
        ),
        context,
    )
    assert not updated_result.is_error
    assert json.loads(updated_result.output)["updated"]["status"] == "一面"

    listed_result = await ApplicationListTool().execute(
        ApplicationListToolInput(company="云帆科技"),
        context,
    )
    listed = json.loads(listed_result.output)
    assert listed["summary"]["total"] == 1
    assert listed["applications"][0]["status"] == "一面"


def test_setup_persisted_state_is_readable_by_store(
    isolated_jobhunt: dict[str, Path | Settings],
) -> None:
    """Keep the storage contract explicit for first-run and restart scenarios."""

    jobhunt_dir = isolated_jobhunt["jobhunt_dir"]
    assert isinstance(jobhunt_dir, Path)
    store = JobHuntStore(jobhunt_dir)
    profile = store.merge_profile(
        {"basic": {"current_title": "Python 后端工程师"}, "skills": {"technical": ["Python"]}}
    )
    assert store.load_profile() == profile
