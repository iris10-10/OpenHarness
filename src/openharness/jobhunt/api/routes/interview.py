"""Interview preparation endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from openharness.jobhunt.api.schemas import InterviewPracticeRequest
from openharness.tools.base import ToolExecutionContext
from openharness.tools.interview_tool import InterviewQuestionsTool, InterviewQuestionsToolInput

router = APIRouter(prefix="/interview", tags=["interview"])


@router.post("/practice")
async def practice(request: InterviewPracticeRequest) -> dict[str, object]:
    jd_text = request.jd_text or f"公司：{request.company}\n岗位：{request.position}\n技能要求：Python React 系统设计 沟通协作"
    result = await InterviewQuestionsTool().execute(
        InterviewQuestionsToolInput(
            jd_text=jd_text,
            company=request.company,
            round=request.round,
            count=request.count,
        ),
        ToolExecutionContext(cwd="."),
    )
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.output)
    return json.loads(result.output)
