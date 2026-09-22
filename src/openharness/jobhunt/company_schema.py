"""Canonical company records projected from the local job snapshot."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from openharness.jobhunt.job_schema import SourceRegistry, sanitize_external_text

_SPACE_RE = re.compile(r"\s+")


def normalize_company_key(value: Any) -> str:
    """Return a stable comparison key without changing the displayed name."""

    text = unicodedata.normalize("NFKC", sanitize_external_text(value, max_chars=200))
    return _SPACE_RE.sub("", text).lower()


def _company_record_id(key: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    if safe:
        return f"company:{safe[:72]}"
    return f"company:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:20]}"


@dataclass
class CompanyRecord:
    """Company library record derived from one or more canonical job records."""

    id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    source_codes: list[str] = field(default_factory=list)
    official_career_url: str = ""
    job_count: int = 0
    cities: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    latest_job_published_at: str = ""
    last_synced_at: str = ""
    status: str = "stale"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "source_codes": list(self.source_codes),
            "official_career_url": self.official_career_url,
            "job_count": self.job_count,
            "cities": list(self.cities),
            "departments": list(self.departments),
            "latest_job_published_at": self.latest_job_published_at,
            "last_synced_at": self.last_synced_at,
            "status": self.status,
        }


def project_companies(
    jobs: Iterable[Mapping[str, Any]],
    *,
    registry: SourceRegistry,
    synced_at: str,
) -> list[dict[str, Any]]:
    """Aggregate company library records from the canonical job snapshot."""

    grouped: dict[str, CompanyRecord] = {}
    alias_to_key: dict[str, str] = {}
    for registration in registry.registrations():
        canonical = registration.default_company
        if not canonical:
            continue
        source_key = (
            f"id:{registration.company_id}"
            if registration.company_id
            else normalize_company_key(canonical)
        )
        alias_to_key[normalize_company_key(canonical)] = source_key
        for alias in registration.aliases:
            alias_to_key[normalize_company_key(alias)] = source_key

    for job in jobs:
        raw_name = sanitize_external_text(job.get("company"), max_chars=200)
        if not raw_name:
            continue
        source_code = str(job.get("source_code") or "").strip()
        registration = registry.get(source_code)
        source_key = alias_to_key.get(normalize_company_key(raw_name))
        if registration and registration.company_id:
            source_key = f"id:{registration.company_id}"
        source_key = source_key or normalize_company_key(raw_name)
        if not source_key:
            continue

        company = grouped.get(source_key)
        if company is None:
            display_name = (
                registration.default_company
                if registration and registration.default_company and source_key.startswith("id:")
                else raw_name
            )
            company = CompanyRecord(
                id=_company_record_id(source_key),
                name=display_name,
                last_synced_at=synced_at,
            )
            grouped[source_key] = company

        if raw_name != company.name and raw_name not in company.aliases:
            company.aliases.append(raw_name)
        if registration:
            for alias in registration.aliases:
                if alias != company.name and alias not in company.aliases:
                    company.aliases.append(alias)
        if source_code and source_code not in company.source_codes:
            company.source_codes.append(source_code)
        if registration and registration.official_career_url and not company.official_career_url:
            company.official_career_url = registration.official_career_url
        company.job_count += 1

        city = sanitize_external_text(job.get("city"), max_chars=100)
        if city and city not in company.cities:
            company.cities.append(city)
        department = sanitize_external_text(
            job.get("department") or job.get("direction"), max_chars=120
        )
        if department and department not in company.departments:
            company.departments.append(department)
        if registration:
            for department in registration.departments:
                if department not in company.departments:
                    company.departments.append(department)

        published = str(job.get("published_at") or job.get("posted_date") or "").strip()
        company.latest_job_published_at = max(company.latest_job_published_at, published)
        last_seen = str(job.get("last_seen_at") or job.get("fetched_at") or "").strip()
        company.last_synced_at = max(company.last_synced_at, last_seen)
        if str(job.get("status") or "active") == "active":
            company.status = "active"

    records = []
    for company in grouped.values():
        company.aliases = sorted(dict.fromkeys(company.aliases))
        company.source_codes.sort()
        company.cities.sort()
        company.departments.sort()
        records.append(company.to_dict())
    records.sort(key=lambda item: str(item.get("name") or "").lower())
    return records
