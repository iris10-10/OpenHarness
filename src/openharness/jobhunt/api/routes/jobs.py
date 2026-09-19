"""Job search and matching endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from openharness.jobhunt.api.deps import load_jobs, save_jobs
from openharness.jobhunt.api.schemas import JobCreateRequest, JobMatchRequest, JobSearchRequest
from openharness.jobhunt.parsing import parse_jd_text
from openharness.jobhunt.scoring import CandidateProfile, score_match
from openharness.jobhunt.storage import new_record_id, today_iso, utc_now_iso

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _seed_jobs() -> list[dict[str, Any]]:
    return [
        {
            "id": "job-demo-frontend",
            "title": "高级前端工程师",
            "company": "星河智能",
            "city": "上海",
            "salary_min": 28,
            "salary_max": 45,
            "experience": "3-5年",
            "education": "本科",
            "direction": "前端",
            "company_type": "AI 初创",
            "tags": ["React", "TypeScript", "可视化"],
            "match_score": 86,
            "posted_date": today_iso(),
            "jd_text": "岗位：高级前端工程师\n公司：星河智能\n地点：上海\n要求：React TypeScript Vite 数据可视化，3年以上经验，本科。",
            "url": "",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
        {
            "id": "job-demo-backend",
            "title": "Python 后端工程师",
            "company": "云帆科技",
            "city": "杭州",
            "salary_min": 25,
            "salary_max": 40,
            "experience": "3-5年",
            "education": "本科",
            "direction": "后端",
            "company_type": "成长型",
            "tags": ["Python", "FastAPI", "Redis", "MySQL"],
            "match_score": 82,
            "posted_date": today_iso(),
            "jd_text": "岗位：Python 后端工程师\n公司：云帆科技\n地点：杭州\n要求：Python FastAPI Redis MySQL 微服务，3年以上经验，本科。",
            "url": "",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        },
    ]


def _jobs() -> list[dict[str, Any]]:
    jobs = load_jobs()
    if jobs:
        return jobs
    save_jobs(_seed_jobs())
    return load_jobs()


def _salary_ok(job: dict[str, Any], salary_min: int | None) -> bool:
    if salary_min is None:
        return True
    try:
        return int(job.get("salary_max") or 0) >= salary_min
    except (TypeError, ValueError):
        return False


def _filter_jobs(jobs: list[dict[str, Any]], request: JobSearchRequest) -> list[dict[str, Any]]:
    query = request.query.strip().lower()
    matched = []
    for job in jobs:
        haystack = " ".join(
            str(job.get(key, ""))
            for key in ("title", "company", "city", "direction", "company_type", "jd_text")
        ).lower()
        haystack += " " + " ".join(str(tag) for tag in job.get("tags", []))
        if query and query not in haystack:
            continue
        if request.city and request.city != str(job.get("city", "")):
            continue
        if request.direction and request.direction != str(job.get("direction", "")):
            continue
        if request.company_type and request.company_type != str(job.get("company_type", "")):
            continue
        if not _salary_ok(job, request.salary_min):
            continue
        matched.append(job)
    return matched


@router.get("")
def list_jobs(
    query: str = "",
    city: str = "",
    direction: str = "",
    company_type: str = "",
    salary_min: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> dict[str, object]:
    request = JobSearchRequest(
        query=query,
        city=city,
        direction=direction,
        company_type=company_type,
        salary_min=salary_min,
        page=page,
        page_size=page_size,
    )
    matched = _filter_jobs(_jobs(), request)
    start = (page - 1) * page_size
    return {"items": matched[start : start + page_size], "total": len(matched), "page": page, "page_size": page_size}


@router.get("/{job_id}")
def detail(job_id: str) -> dict[str, object]:
    job = next((item for item in _jobs() if str(item.get("id")) == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": job}


@router.post("/search")
def search(request: JobSearchRequest) -> dict[str, object]:
    return list_jobs(
        query=request.query,
        city=request.city,
        direction=request.direction,
        company_type=request.company_type,
        salary_min=request.salary_min,
        page=request.page,
        page_size=request.page_size,
    )


@router.post("/import")
def import_job(request: JobCreateRequest) -> dict[str, object]:
    jd = parse_jd_text(
        request.jd_text or f"岗位：{request.title}\n公司：{request.company}\n地点：{request.city}",
        title_hint=request.title,
        company_hint=request.company,
        city_hint=request.city,
        url=request.url,
        posted_date=request.posted_date or today_iso(),
    )
    job = {
        "id": new_record_id("job"),
        "title": request.title or jd.title,
        "company": request.company or jd.company,
        "city": request.city or jd.city,
        "salary_min": request.salary_min,
        "salary_max": request.salary_max,
        "experience": request.experience or jd.experience,
        "education": request.education or jd.education,
        "direction": request.direction or jd.category or "",
        "company_type": request.company_type,
        "tags": request.tags or jd.all_skills[:8],
        "match_score": 0,
        "posted_date": request.posted_date or today_iso(),
        "jd_text": request.jd_text,
        "url": request.url,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    jobs = [*_jobs(), job]
    save_jobs(jobs)
    return {"job": job, "total": len(jobs)}


@router.post("/match")
def match(request: JobMatchRequest) -> dict[str, object]:
    jobs = _jobs()
    if request.job_ids:
        jobs = [job for job in jobs if str(job.get("id")) in request.job_ids]
    candidate = CandidateProfile.from_resume(parse_jd_text("").__class__()) if False else None
    if request.resume_text.strip():
        from openharness.jobhunt.parsing import parse_resume_text

        candidate = CandidateProfile.from_resume(parse_resume_text(request.resume_text))
    if candidate is None:
        candidate = CandidateProfile.from_profile_dict({})
    results = []
    for job in jobs:
        jd = parse_jd_text(
            str(job.get("jd_text") or ""),
            title_hint=str(job.get("title") or ""),
            company_hint=str(job.get("company") or ""),
            city_hint=str(job.get("city") or ""),
            posted_date=str(job.get("posted_date") or ""),
        )
        score = score_match(candidate, jd)
        enriched = dict(job)
        enriched["match_score"] = round(score.total, 1)
        enriched["match_breakdown"] = {
            item.name: {"score": item.score, "weight": item.weight, "detail": item.detail}
            for item in score.dimensions
        }
        enriched["recommendation"] = score.recommendation
        enriched["missing_skills"] = list(score.missing_skills)
        results.append(enriched)
    results.sort(key=lambda item: float(item.get("match_score") or 0), reverse=True)
    return {"items": results, "total": len(results)}


@router.delete("/{job_id}")
def delete(job_id: str) -> dict[str, object]:
    jobs = _jobs()
    kept = [job for job in jobs if str(job.get("id")) != job_id]
    if len(kept) == len(jobs):
        raise HTTPException(status_code=404, detail="job not found")
    save_jobs(kept)
    return {"ok": True, "total": len(kept)}
