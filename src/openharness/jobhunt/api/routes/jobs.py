"""Job search and matching endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from openharness.config.settings import load_settings
from openharness.jobhunt.api.deps import get_store, load_jobs, save_jobs
from openharness.jobhunt.api.schemas import (
    JobCreateRequest,
    JobMatchRequest,
    JobSearchRequest,
    JobSyncRequest,
)
from openharness.jobhunt.job_provider import (
    JobSearchQuery,
    JobSearchService,
    JobSyncLimits,
    UnsafeJobProviderError,
    build_jobs_provider_from_scraping_settings,
)
from openharness.jobhunt.job_schema import SourceRegistry, SourceValidationError
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
            "source_url": "",
            "apply_url": "",
            "source_code": "local_demo",
            "source_site": "OpenHarness 示例岗位",
            "provider": "local_demo",
            "provider_record_id": "job-demo-frontend",
            "published_at": today_iso(),
            "fetched_at": utc_now_iso(),
            "last_seen_at": utc_now_iso(),
            "provenance_status": "unverified",
            "status": "active",
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
            "source_url": "",
            "apply_url": "",
            "source_code": "local_demo",
            "source_site": "OpenHarness 示例岗位",
            "provider": "local_demo",
            "provider_record_id": "job-demo-backend",
            "published_at": today_iso(),
            "fetched_at": utc_now_iso(),
            "last_seen_at": utc_now_iso(),
            "provenance_status": "unverified",
            "status": "active",
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


def _source_registry() -> SourceRegistry:
    """Load only the reviewed source registrations used by the Web boundary."""

    try:
        settings = load_settings()
        return SourceRegistry.from_mappings(settings.scraping.allowed_sources)
    except Exception:  # noqa: BLE001
        # A malformed provider configuration must not make local search
        # unavailable. It does, however, make every external link untrusted.
        return SourceRegistry()


def _validated_job_links(job: dict[str, Any]) -> tuple[str, str, str]:
    """Return safe links plus a provenance status adjustment."""

    source_url = str(job.get("source_url") or job.get("url") or "").strip()
    apply_url = str(job.get("apply_url") or source_url).strip()
    if not source_url and not apply_url:
        return "", "", str(job.get("provenance_status") or "unverified")

    source_code = str(job.get("source_code") or "").strip()
    registration = _source_registry().get(source_code)
    if registration is None:
        return "", "", "invalid"
    try:
        safe_source = registration.validate_url(source_url, field_name="source_url")
        safe_apply = registration.validate_url(apply_url, field_name="apply_url")
    except SourceValidationError:
        return "", "", "invalid"
    return safe_source, safe_apply, str(job.get("provenance_status") or "verified")


def _validate_manual_links(source_url: str, apply_url: str) -> tuple[str, str, str, str]:
    """Validate manual-import links against the configured source registry."""

    raw_source = source_url.strip()
    raw_apply = apply_url.strip()
    if not raw_source and not raw_apply:
        return "", "", "manual_import", "用户手动导入"
    if not raw_source:
        raise HTTPException(status_code=422, detail="手动导入必须提供原始岗位链接 source_url。")
    registry = _source_registry()
    for registration in registry.registrations():
        try:
            safe_source = registration.validate_url(raw_source, field_name="source_url")
            safe_apply = registration.validate_url(raw_apply or raw_source, field_name="apply_url")
        except SourceValidationError:
            continue
        return safe_source, safe_apply, registration.source_code, registration.source_site
    raise HTTPException(
        status_code=422,
        detail="岗位链接不在已登记的来源域名白名单中，不能写入岗位库。",
    )


def _decorate_job(job: dict[str, Any]) -> dict[str, Any]:
    """Expose the canonical provenance fields while keeping old jobs readable."""

    payload = dict(job)
    source_url, apply_url, link_status = _validated_job_links(payload)
    payload["source_url"] = source_url
    payload["apply_url"] = apply_url
    payload.setdefault("source_code", "legacy_local")
    payload.setdefault("source_site", "本地岗位库")
    payload.setdefault("provider", "local")
    payload.setdefault("provider_record_id", str(payload.get("id") or ""))
    payload.setdefault("published_at", str(payload.get("posted_date") or ""))
    payload.setdefault("fetched_at", str(payload.get("updated_at") or payload.get("created_at") or ""))
    payload.setdefault("last_seen_at", payload["fetched_at"])
    payload.setdefault("provenance_status", link_status)
    if link_status == "invalid":
        payload["provenance_status"] = "invalid"
    payload.setdefault("status", "active")
    payload.setdefault("description", str(payload.get("description") or payload.get("jd_text") or ""))
    return payload


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
        job = _decorate_job(job)
        haystack = " ".join(
            str(job.get(key, ""))
            for key in (
                "title",
                "company",
                "city",
                "direction",
                "company_type",
                "jd_text",
                "description",
                "source_site",
            )
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
        if request.salary_max is not None:
            try:
                if int(job.get("salary_min") or 0) > request.salary_max:
                    continue
            except (TypeError, ValueError):
                continue
        if request.experience and request.experience not in str(job.get("experience", "")):
            continue
        if request.education and request.education not in str(job.get("education", "")):
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
    salary_max: int | None = None,
    experience: str = "",
    education: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> dict[str, object]:
    effective_page = page if isinstance(page, int) else 1
    effective_page_size = page_size if isinstance(page_size, int) else 10
    request = JobSearchRequest(
        query=query,
        city=city,
        direction=direction,
        company_type=company_type,
        salary_min=salary_min,
        salary_max=salary_max,
        experience=experience,
        education=education,
        page=effective_page,
        page_size=effective_page_size,
    )
    matched = [_decorate_job(job) for job in _filter_jobs(_jobs(), request)]
    start = (effective_page - 1) * effective_page_size
    return {
        "items": matched[start : start + effective_page_size],
        "total": len(matched),
        "page": effective_page,
        "page_size": effective_page_size,
    }


@router.post("/search")
def search(request: JobSearchRequest) -> dict[str, object]:
    return list_jobs(
        query=request.query,
        city=request.city,
        direction=request.direction,
        company_type=request.company_type,
        salary_min=request.salary_min,
        salary_max=request.salary_max,
        experience=request.experience,
        education=request.education,
        page=request.page,
        page_size=request.page_size,
    )


def _build_sync_service() -> tuple[JobSearchService, Any]:
    """Build one short-lived, reviewed, read-only provider adapter."""

    settings = load_settings()
    scraping = settings.scraping
    if not scraping.enabled:
        raise HTTPException(
            status_code=409,
            detail="岗位同步未启用。请先配置 scraping.enabled=true。",
        )
    if not scraping.account_safe_mode:
        raise HTTPException(
            status_code=409,
            detail="账号安全模式已关闭，Web 页面拒绝执行外部岗位同步。",
        )
    registry = SourceRegistry.from_mappings(scraping.allowed_sources)
    try:
        server_config = (
            settings.mcp_servers.get(scraping.provider_server)
            if scraping.provider_server.strip()
            else None
        )
        provider = build_jobs_provider_from_scraping_settings(
            scraping,
            registry=registry,
            mcp_server_config=server_config,
        )
    except UnsafeJobProviderError as exc:
        raise HTTPException(status_code=409, detail=f"岗位服务配置不安全: {exc}") from exc

    rag_store = None
    if settings.rag.enabled:
        try:
            from openharness.rag import build_retriever_from_settings

            rag_store = build_retriever_from_settings(settings).store
        except Exception:  # noqa: BLE001
            rag_store = None
    service = JobSearchService(
        get_store(),
        registry=registry,
        provider=provider,
        rag_store=rag_store,
        limits=JobSyncLimits(
            max_results=scraping.max_results,
            max_pages=scraping.max_pages,
            max_details=scraping.max_details,
            max_response_bytes=scraping.max_response_bytes,
            max_retries=min(scraping.max_retries, 2),
            freshness_hours=scraping.cache_freshness_hours,
        ),
    )
    return service, settings


@router.post("/sync")
def sync_jobs(request: JobSyncRequest) -> dict[str, object]:
    """Synchronize jobs once through the configured read-only provider."""

    service, _settings = _build_sync_service()
    query = JobSearchQuery(
        query=request.query,
        city=request.city,
        salary_min=request.salary_min,
        salary_max=request.salary_max,
        experience=request.experience,
        education=request.education,
        limit=request.limit,
    )
    results, report, notes = service.search(query, force_sync=True)
    return {
        "items": [_decorate_job(item) for item in results],
        "total": len(results),
        "report": report.to_dict() if report is not None else None,
        "notes": notes,
    }


@router.get("/sync/status")
def sync_status() -> dict[str, object]:
    """Return safe provider configuration and redacted synchronization history."""

    settings = load_settings()
    scraping = settings.scraping
    sources = [
        {
            "source_code": str(item.get("source_code") or item.get("code") or ""),
            "source_site": str(item.get("source_site") or item.get("site") or ""),
            "allowed_domains": list(item.get("allowed_domains") or item.get("domains") or []),
        }
        for item in scraping.allowed_sources
        if isinstance(item, dict)
    ]
    return {
        "account_safe_mode": scraping.account_safe_mode,
        "sync_enabled": scraping.enabled,
        "provider_configured": bool(
            scraping.provider_server
            or any(
                isinstance(item, dict)
                and (item.get("search_url_template") or item.get("feed_url"))
                for item in scraping.allowed_sources
            )
        ),
        "provider_name": scraping.provider_name or "openharness_public_jobs",
        "provider_server": scraping.provider_server,
        "allowed_sources": sources,
        "limits": {
            "max_results": scraping.max_results,
            "max_pages": scraping.max_pages,
            "max_details": scraping.max_details,
            "max_response_bytes": scraping.max_response_bytes,
            "freshness_hours": scraping.cache_freshness_hours,
        },
        "recent_runs": get_store().load_sync_runs(limit=20),
    }


@router.get("/{job_id}")
def detail(job_id: str) -> dict[str, object]:
    job = next((item for item in _jobs() if str(item.get("id")) == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job": _decorate_job(job)}


@router.post("/import")
def import_job(request: JobCreateRequest) -> dict[str, object]:
    source_url, apply_url, source_code, source_site = _validate_manual_links(
        request.source_url or request.url,
        request.apply_url,
    )
    jd = parse_jd_text(
        request.jd_text or f"岗位：{request.title}\n公司：{request.company}\n地点：{request.city}",
        title_hint=request.title,
        company_hint=request.company,
        city_hint=request.city,
        url=source_url,
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
        "url": source_url,
        "source_url": source_url,
        "apply_url": apply_url,
        "source_code": source_code,
        "source_site": source_site,
        "provider": "local",
        "provider_record_id": "",
        "published_at": request.posted_date or today_iso(),
        "fetched_at": utc_now_iso(),
        "last_seen_at": utc_now_iso(),
        "provenance_status": "unverified",
        "status": "active",
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }
    jobs = [*_jobs(), job]
    save_jobs(jobs)
    return {"job": _decorate_job(job), "total": len(jobs)}


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
