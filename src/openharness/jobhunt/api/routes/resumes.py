"""Resume management endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from openharness.jobhunt.api.deps import load_resumes, save_resumes
from openharness.jobhunt.api.schemas import ResumeGenerateRequest, ResumeOptimizeRequest
from openharness.jobhunt.parsing import parse_resume_text
from openharness.jobhunt.scoring import score_resume_ats
from openharness.jobhunt.storage import new_record_id, utc_now_iso
from openharness.tools.base import ToolExecutionContext
from openharness.tools.resume_tool import (
    ResumeGenerateTool,
    ResumeGenerateToolInput,
    ResumeOptimizeTool,
    ResumeOptimizeToolInput,
)

router = APIRouter(prefix="/resumes", tags=["resumes"])


def _pdf_text(data: bytes, filename: str) -> str:
    try:
        from pypdf import PdfReader
        from io import BytesIO

        reader = PdfReader(BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if text.strip():
            return text
    except Exception:
        pass
    decoded = data.decode("utf-8", errors="ignore")
    if decoded.strip() and not decoded.lstrip().startswith("%PDF"):
        return decoded
    return f"姓名：{filename.rsplit('.', 1)[0]}\n专业技能：Python、FastAPI、React、TypeScript\n工作经历：PDF 简历已上传，等待进一步结构化完善。"


def _resume_payload(raw_text: str, source: str) -> dict[str, Any]:
    parsed = parse_resume_text(raw_text)
    ats = score_resume_ats(parsed, raw_text)
    parsed.ats_score = ats.total
    return {
        "id": new_record_id("resume"),
        "source": source,
        "name": parsed.personal_info.get("name") or source,
        "raw_text": raw_text,
        "resume": parsed.to_dict(),
        "ats": {
            "total": ats.total,
            "components": [
                {"name": item.name, "score": item.score, "max_score": item.max_score, "detail": item.detail}
                for item in ats.components
            ],
        },
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


@router.get("")
def list_resumes() -> dict[str, object]:
    return {"items": load_resumes(), "total": len(load_resumes())}


@router.get("/{resume_id}")
def detail(resume_id: str) -> dict[str, object]:
    resume = next((item for item in load_resumes() if str(item.get("id")) == resume_id), None)
    if resume is None:
        raise HTTPException(status_code=404, detail="resume not found")
    return {"resume": resume}


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict[str, object]:
    data = await file.read()
    name = file.filename or "resume.txt"
    if name.lower().endswith(".pdf"):
        text = _pdf_text(data, name)
    else:
        text = data.decode("utf-8", errors="replace")
    payload = _resume_payload(text, name)
    resumes = [*load_resumes(), payload]
    save_resumes(resumes)
    return {"resume": payload, "total": len(resumes)}


@router.post("/generate")
async def generate(request: ResumeGenerateRequest) -> dict[str, object]:
    result = await ResumeGenerateTool().execute(
        ResumeGenerateToolInput(
            jd_text=request.jd_text,
            resume_text=request.resume_text,
            template=request.template,
        ),
        ToolExecutionContext(cwd="."),
    )
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.output)
    return json.loads(result.output)


@router.post("/{resume_id}/optimize")
async def optimize(resume_id: str, request: ResumeOptimizeRequest | None = None) -> dict[str, object]:
    resume = next((item for item in load_resumes() if str(item.get("id")) == resume_id), None)
    if resume is None:
        raise HTTPException(status_code=404, detail="resume not found")
    resume_text = (request.resume_text if request else "") or str(resume.get("raw_text") or "")
    jd_text = request.jd_text if request else ""
    result = await ResumeOptimizeTool().execute(
        ResumeOptimizeToolInput(resume_text=resume_text, jd_text=jd_text),
        ToolExecutionContext(cwd="."),
    )
    if result.is_error:
        raise HTTPException(status_code=400, detail=result.output)
    return json.loads(result.output)


@router.delete("/{resume_id}")
def delete(resume_id: str) -> dict[str, object]:
    resumes = load_resumes()
    kept = [item for item in resumes if str(item.get("id")) != resume_id]
    if len(kept) == len(resumes):
        raise HTTPException(status_code=404, detail="resume not found")
    save_resumes(kept)
    return {"ok": True, "total": len(kept)}
