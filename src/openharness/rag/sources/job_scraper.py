"""Recruiting-platform scrapers for job postings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

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
class JobPosting:
    """Structured job posting captured from recruiting platforms."""

    title: str
    company: str
    city: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    description: str = ""
    company_size: str = ""
    industry: str = ""
    financing: str = ""
    published_at: str = ""
    url: str = ""
    source: str = ""

    def to_document(self) -> ScrapedDocument:
        """Convert the posting into a vector-store document payload."""

        lines = [
            f"岗位: {self.title}",
            f"公司: {self.company}",
            f"城市: {self.city}",
            f"薪资: {self.salary}",
            f"经验: {self.experience}",
            f"学历: {self.education}",
            f"公司规模: {self.company_size}",
            f"行业: {self.industry}",
            f"融资阶段: {self.financing}",
            f"发布时间: {self.published_at}",
            "",
            self.description,
        ]
        metadata = {
            "source": self.url,
            "doc_type": "job_posting",
            "source_site": self.source,
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "salary": self.salary,
            "experience": self.experience,
            "education": self.education,
            "company_size": self.company_size,
            "industry": self.industry,
            "financing": self.financing,
            "published_at": self.published_at,
            "url": self.url,
        }
        doc_id = f"job:{self.source}:{self.url or self.title + self.company}"
        return ScrapedDocument(id=doc_id, text=normalize_space("\n".join(lines)), metadata=metadata)


class JobScraperBase(BaseScraper):
    """Legacy parser compatibility layer, not a production job source.

    The live Boss/Lagou page collectors are retired in favor of the reviewed
    read-only Jobs provider boundary.  A caller may still inject a lightweight
    fixture client for parser regression tests, but the default scraper can no
    longer open a recruitment-site network connection.
    """

    source_name = "jobs"
    search_url = ""

    def fetch(self, url: str, *, params: Mapping[str, Any] | None = None) -> str:
        """Allow deterministic fixture clients only; block live job scraping."""

        if self._client is None:
            raise PermissionError(
                "live job-board scraping is retired; configure a reviewed read-only Jobs provider"
            )
        if isinstance(self._client, httpx.Client):
            raise PermissionError(
                "direct HTTP clients are not allowed for job-board scraping; use a reviewed Jobs provider"
            )
        if self.config.cookies or self.config.proxies:
            raise PermissionError(
                "job-board cookies and proxies are forbidden by the account-safe job boundary"
            )
        return super().fetch(url, params=params)

    def build_search_url(self, query: str, *, city: str = "", page: int = 1) -> str:
        """Build a source-specific search URL."""

        params = {"query": query, "page": page}
        if city:
            params["city"] = city
        return f"{self.search_url}?{urlencode(params)}"

    def collect(
        self,
        query: str,
        *,
        city: str = "",
        pages: int = 1,
    ) -> ScrapeReport:
        """Collect job postings from search pages."""

        report = ScrapeReport()
        newest = self.high_watermark()
        for page in range(1, max(1, pages) + 1):
            search_url = self.build_search_url(query, city=city, page=page)
            try:
                page_text = self.fetch(search_url)
                report.fetched += 1
            except (RuntimeError, PermissionError, OSError) as exc:
                report.failed += 1
                report.errors.append(f"{search_url}: {exc}")
                continue
            postings = self.parse_search_results(page_text, base_url=search_url)
            for posting in postings:
                if not posting.url:
                    posting = _merge_job(posting, url=search_url)
                if not self.should_collect(posting.url, updated_at=posting.published_at):
                    report.skipped_duplicates += 1
                    continue
                detail = posting
                if not detail.description and detail.url != search_url:
                    try:
                        detail_text = self.fetch(detail.url)
                        report.fetched += 1
                        detail = self.parse_job_detail(detail_text, base_url=detail.url, seed=posting)
                    except (RuntimeError, PermissionError, OSError) as exc:
                        report.errors.append(f"{detail.url}: {exc}")
                report.documents.append(detail.to_document())
                self.mark_seen_url(detail.url, updated_at=detail.published_at)
                if detail.published_at and (newest is None or detail.published_at > newest):
                    newest = detail.published_at
        if newest:
            self.set_high_watermark(newest)
        return report

    def parse_search_results(self, text: str, *, base_url: str = "") -> list[JobPosting]:
        """Parse job cards from JSON payloads or simple semantic HTML blocks."""

        postings = self._parse_json_jobs(text, base_url=base_url)
        if postings:
            return postings
        return self._parse_html_jobs(text, base_url=base_url)

    def parse_job_detail(
        self,
        text: str,
        *,
        base_url: str = "",
        seed: JobPosting | None = None,
    ) -> JobPosting:
        """Parse a detail page and merge it over an optional search-card seed."""

        parsed = self.parse_search_results(text, base_url=base_url)
        detail = parsed[0] if parsed else _job_from_attrs({}, text, base_url, self.source_name)
        if seed is None:
            return detail
        return _merge_job(
            seed,
            title=detail.title,
            company=detail.company,
            city=detail.city,
            salary=detail.salary,
            experience=detail.experience,
            education=detail.education,
            description=detail.description,
            company_size=detail.company_size,
            industry=detail.industry,
            financing=detail.financing,
            published_at=detail.published_at,
            url=detail.url or seed.url,
        )

    def _parse_json_jobs(self, text: str, *, base_url: str) -> list[JobPosting]:
        records: list[JobPosting] = []
        for payload in extract_json_payloads(text):
            for item in find_json_objects(
                payload,
                {
                    "jobName",
                    "positionName",
                    "title",
                    "companyName",
                    "brandName",
                    "salaryDesc",
                },
            ):
                posting = _job_from_mapping(item, base_url, self.source_name)
                if posting.title and posting.company:
                    records.append(posting)
        return _dedupe_jobs(records)

    def _parse_html_jobs(self, text: str, *, base_url: str) -> list[JobPosting]:
        block_re = re.compile(
            r"<(?P<tag>article|div|li)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
            re.IGNORECASE | re.DOTALL,
        )
        records: list[JobPosting] = []
        for match in block_re.finditer(text):
            attrs = parse_attrs(match.group("attrs"))
            classes = attrs.get("class", "")
            if "job-card" not in classes and "job" not in attrs.get("data-type", ""):
                continue
            posting = _job_from_attrs(attrs, match.group("body"), base_url, self.source_name)
            if posting.title and posting.company:
                records.append(posting)
        if records:
            return _dedupe_jobs(records)
        return [_job_from_attrs({}, text, base_url, self.source_name)]


class BossScraper(JobScraperBase):
    """Boss Zhipin job scraper.

    Real sites may require user cookies. The parser is intentionally testable
    with captured/mock HTML or JSON so production collection can be reviewed
    before hitting a live endpoint.
    """

    source_name = "boss"
    search_url = "https://www.zhipin.com/web/geek/job"


class LagouScraper(JobScraperBase):
    """Lagou job scraper."""

    source_name = "lagou"
    search_url = "https://www.lagou.com/jobs/list"


def _first(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return normalize_space(str(value))
    return ""


def _absolute_url(raw_url: str, base_url: str) -> str:
    raw = raw_url.strip()
    if not raw:
        return base_url
    return urljoin(base_url, raw)


def _job_from_mapping(item: Mapping[str, Any], base_url: str, source: str) -> JobPosting:
    raw_url = _first(item, "url", "jobUrl", "positionUrl", "href", "link")
    description = _first(item, "description", "jobDescription", "detail", "content")
    if not description and isinstance(item.get("jobLabels"), list):
        description = " ".join(str(label) for label in item["jobLabels"])
    return JobPosting(
        title=_first(item, "title", "jobName", "positionName", "name"),
        company=_first(item, "company", "companyName", "brandName"),
        city=_first(item, "city", "cityName", "workCity"),
        salary=_first(item, "salary", "salaryDesc", "salaryRange"),
        experience=_first(item, "experience", "experienceName", "workYear", "jobExperience"),
        education=_first(item, "education", "degreeName", "educationName"),
        description=description,
        company_size=_first(item, "companySize", "scaleName", "staffSize"),
        industry=_first(item, "industry", "industryName", "industryField"),
        financing=_first(item, "financing", "financeStage", "financeStageName"),
        published_at=_first(item, "publishedAt", "publishTime", "updateTime", "createTime"),
        url=_absolute_url(raw_url, base_url),
        source=source,
    )


def _job_from_attrs(attrs: Mapping[str, str], body: str, base_url: str, source: str) -> JobPosting:
    anchor = re.search(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", body, re.IGNORECASE | re.DOTALL)
    anchor_attrs = parse_attrs(anchor.group("attrs")) if anchor else {}
    title = _pick_attr(attrs, "data-title", "data-job", "data-position") or (
        strip_html(anchor.group("body")) if anchor else ""
    )
    description = _pick_by_class(body, "description", "job-description", "detail")
    if not description:
        description = strip_html(body)
    return JobPosting(
        title=title,
        company=_pick_attr(attrs, "data-company") or _pick_by_class(body, "company"),
        city=_pick_attr(attrs, "data-city") or _pick_by_class(body, "city"),
        salary=_pick_attr(attrs, "data-salary") or _pick_by_class(body, "salary"),
        experience=_pick_attr(attrs, "data-experience") or _pick_by_class(body, "experience"),
        education=_pick_attr(attrs, "data-education") or _pick_by_class(body, "education"),
        description=description,
        company_size=_pick_attr(attrs, "data-company-size") or _pick_by_class(body, "company-size"),
        industry=_pick_attr(attrs, "data-industry") or _pick_by_class(body, "industry"),
        financing=_pick_attr(attrs, "data-financing") or _pick_by_class(body, "financing"),
        published_at=_pick_attr(attrs, "data-published-at") or _pick_by_class(body, "published-at"),
        url=_absolute_url(anchor_attrs.get("href", attrs.get("data-url", "")), base_url),
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


def _merge_job(seed: JobPosting, **updates: str) -> JobPosting:
    values = seed.__dict__.copy()
    for key, value in updates.items():
        if value:
            values[key] = value
    return JobPosting(**values)


def _dedupe_jobs(records: list[JobPosting]) -> list[JobPosting]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[JobPosting] = []
    for record in records:
        key = (record.url, record.title, record.company)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique
