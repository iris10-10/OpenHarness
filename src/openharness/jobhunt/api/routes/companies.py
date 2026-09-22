"""Company library endpoints projected from synchronized job records."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from openharness.config.settings import load_settings
from openharness.jobhunt.api.deps import get_store, load_jobs
from openharness.jobhunt.company_schema import project_companies
from openharness.jobhunt.job_schema import SourceRegistry

router = APIRouter(prefix="/companies", tags=["companies"])


def _companies() -> list[dict[str, Any]]:
    store = get_store()
    companies = store.load_companies()
    if companies:
        return companies
    companies = project_companies(
        store.load_jobs(),
        registry=SourceRegistry.from_mappings(load_settings().scraping.allowed_sources),
        synced_at="",
    )
    if companies:
        store.save_companies(companies)
    return companies


@router.get("")
def list_companies(
    query: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict[str, object]:
    normalized = query.strip().lower()
    matched = [
        company
        for company in _companies()
        if not normalized
        or normalized in str(company.get("name") or "").lower()
        or any(normalized in str(alias).lower() for alias in company.get("aliases", []))
    ]
    start = (page - 1) * page_size
    return {
        "items": matched[start : start + page_size],
        "total": len(matched),
        "page": page,
        "page_size": page_size,
    }


@router.get("/{company_id}")
def company_detail(company_id: str) -> dict[str, object]:
    company = next((item for item in _companies() if str(item.get("id")) == company_id), None)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    names = {str(company.get("name") or ""), *map(str, company.get("aliases") or [])}
    jobs = [
        job
        for job in load_jobs()
        if str(job.get("company") or "") in names
    ]
    return {"company": company, "jobs": jobs}
