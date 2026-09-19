"""Company-information scrapers for RAG collections."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urljoin

from openharness.rag.sources.base import (
    BaseScraper,
    ScrapedDocument,
    ScrapeReport,
    extract_json_payloads,
    find_json_objects,
    normalize_space,
    parse_attrs,
    strip_html,
)


@dataclass(frozen=True)
class CompanyInfo:
    """Structured company profile or open-source project signal."""

    name: str
    description: str = ""
    industry: str = ""
    financing: str = ""
    size: str = ""
    website: str = ""
    repositories: list[str] = field(default_factory=list)
    updated_at: str = ""
    url: str = ""
    source: str = ""

    def to_document(self) -> ScrapedDocument:
        lines = [
            f"公司/项目: {self.name}",
            f"行业: {self.industry}",
            f"融资阶段: {self.financing}",
            f"规模: {self.size}",
            f"官网: {self.website}",
            f"仓库: {', '.join(self.repositories)}",
            f"更新时间: {self.updated_at}",
            "",
            self.description,
        ]
        metadata = {
            "source": self.url,
            "doc_type": "company_info",
            "source_site": self.source,
            "title": self.name,
            "company": self.name,
            "industry": self.industry,
            "financing": self.financing,
            "size": self.size,
            "website": self.website,
            "updated_at": self.updated_at,
            "url": self.url,
        }
        doc_id = f"company:{self.source}:{self.url or self.name}"
        return ScrapedDocument(id=doc_id, text=normalize_space("\n".join(lines)), metadata=metadata)


class CompanyScraperBase(BaseScraper):
    """Shared scraper flow for company profile sources."""

    source_name = "company"
    search_url = ""

    def build_search_url(self, query: str, *, page: int = 1) -> str:
        return f"{self.search_url}?{urlencode({'q': query, 'page': page})}"

    def collect(self, query: str, *, pages: int = 1) -> ScrapeReport:
        report = ScrapeReport()
        newest = self.high_watermark()
        for page in range(1, max(1, pages) + 1):
            url = self.build_search_url(query, page=page)
            try:
                text = self.fetch(url)
                report.fetched += 1
            except (RuntimeError, PermissionError, OSError) as exc:
                report.failed += 1
                report.errors.append(f"{url}: {exc}")
                continue
            for company in self.parse_companies(text, base_url=url):
                if not company.url:
                    company = _merge_company(company, url=url)
                if not self.should_collect(company.url, updated_at=company.updated_at):
                    report.skipped_duplicates += 1
                    continue
                report.documents.append(company.to_document())
                self.mark_seen_url(company.url, updated_at=company.updated_at)
                if company.updated_at and (newest is None or company.updated_at > newest):
                    newest = company.updated_at
        if newest:
            self.set_high_watermark(newest)
        return report

    def parse_companies(self, text: str, *, base_url: str = "") -> list[CompanyInfo]:
        records = self._parse_json(text, base_url)
        if records:
            return records
        return self._parse_html(text, base_url)

    def _parse_json(self, text: str, base_url: str) -> list[CompanyInfo]:
        records: list[CompanyInfo] = []
        for payload in extract_json_payloads(text):
            for item in find_json_objects(
                payload,
                {"companyName", "name", "industry", "financing", "stargazers_count"},
            ):
                company = _company_from_mapping(item, base_url, self.source_name)
                if company.name:
                    records.append(company)
        return _dedupe(records)

    def _parse_html(self, text: str, base_url: str) -> list[CompanyInfo]:
        block_re = re.compile(
            r"<(?P<tag>article|div|li)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
            re.IGNORECASE | re.DOTALL,
        )
        records: list[CompanyInfo] = []
        for match in block_re.finditer(text):
            attrs = parse_attrs(match.group("attrs"))
            marker = f"{attrs.get('class', '')} {attrs.get('data-type', '')}"
            if "company-card" not in marker and "repo-card" not in marker and "company" not in marker:
                continue
            company = _company_from_attrs(attrs, match.group("body"), base_url, self.source_name)
            if company.name:
                records.append(company)
        if records:
            return _dedupe(records)
        fallback = _company_from_attrs({}, text, base_url, self.source_name)
        return [fallback] if fallback.name else []


class TianyanchaScraper(CompanyScraperBase):
    """Tianyancha company-profile scraper."""

    source_name = "tianyancha"
    search_url = "https://www.tianyancha.com/search"


class MaimaiScraper(CompanyScraperBase):
    """Maimai company/community scraper."""

    source_name = "maimai"
    search_url = "https://maimai.cn/search"


class GithubScraper(CompanyScraperBase):
    """GitHub organization/repository scraper for open-source company signals."""

    source_name = "github"
    search_url = "https://api.github.com/search/repositories"

    def build_search_url(self, query: str, *, page: int = 1) -> str:
        return f"{self.search_url}?{urlencode({'q': query, 'page': page, 'per_page': 20})}"


def _first(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return normalize_space(str(value))
    return ""


def _company_from_mapping(item: Mapping[str, Any], base_url: str, source: str) -> CompanyInfo:
    owner = item.get("owner")
    repo_owner = owner.get("login") if isinstance(owner, dict) else ""
    name = _first(item, "companyName", "company", "full_name", "name") or str(repo_owner or "")
    raw_url = _first(item, "url", "html_url", "companyUrl", "website")
    repositories: list[str] = []
    full_name = _first(item, "full_name")
    if full_name:
        repositories.append(full_name)
    return CompanyInfo(
        name=name,
        description=_first(item, "description", "summary", "intro"),
        industry=_first(item, "industry", "industryName", "category"),
        financing=_first(item, "financing", "financeStage", "round"),
        size=_first(item, "size", "companySize", "staffSize"),
        website=_first(item, "website", "homepage", "homeUrl"),
        repositories=repositories,
        updated_at=_first(item, "updated_at", "updatedAt", "publishTime"),
        url=urljoin(base_url, raw_url) if raw_url else base_url,
        source=source,
    )


def _company_from_attrs(
    attrs: Mapping[str, str],
    body: str,
    base_url: str,
    source: str,
) -> CompanyInfo:
    anchor = re.search(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", body, re.IGNORECASE | re.DOTALL)
    anchor_attrs = parse_attrs(anchor.group("attrs")) if anchor else {}
    repository = _pick_attr(attrs, "data-repository") or _pick_by_class(body, "repository")
    return CompanyInfo(
        name=_pick_attr(attrs, "data-name", "data-company")
        or (strip_html(anchor.group("body")) if anchor else ""),
        description=_pick_by_class(body, "description", "summary", "intro") or strip_html(body),
        industry=_pick_attr(attrs, "data-industry") or _pick_by_class(body, "industry"),
        financing=_pick_attr(attrs, "data-financing") or _pick_by_class(body, "financing"),
        size=_pick_attr(attrs, "data-size") or _pick_by_class(body, "size"),
        website=_pick_attr(attrs, "data-website") or _pick_by_class(body, "website"),
        repositories=[repository] if repository else [],
        updated_at=_pick_attr(attrs, "data-updated-at") or _pick_by_class(body, "updated-at"),
        url=urljoin(base_url, anchor_attrs.get("href", attrs.get("data-url", ""))) or base_url,
        source=source,
    )


def _pick_attr(attrs: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = attrs.get(key)
        if value:
            return normalize_space(value)
    return ""


def _pick_by_class(body: str, *classes: str) -> str:
    for class_name in classes:
        pattern = re.compile(
            rf"<[^>]*class=['\"][^'\"]*{re.escape(class_name)}[^'\"]*['\"][^>]*>(.*?)</[^>]+>",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(body)
        if match:
            return strip_html(match.group(1))
    return ""


def _merge_company(seed: CompanyInfo, **updates: str) -> CompanyInfo:
    values = seed.__dict__.copy()
    for key, value in updates.items():
        if value:
            values[key] = value
    return CompanyInfo(**values)


def _dedupe(records: list[CompanyInfo]) -> list[CompanyInfo]:
    seen: set[tuple[str, str]] = set()
    unique: list[CompanyInfo] = []
    for record in records:
        key = (record.url, record.name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique
