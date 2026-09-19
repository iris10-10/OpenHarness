"""Interview-experience scrapers for Nowcoder and LeetCode discussions."""

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
class InterviewExperience:
    """Structured interview note."""

    title: str
    company: str = ""
    position: str = ""
    round: str = ""
    questions: list[str] = field(default_factory=list)
    answer: str = ""
    background: str = ""
    result: str = ""
    interview_date: str = ""
    content: str = ""
    url: str = ""
    source: str = ""

    def to_document(self) -> ScrapedDocument:
        lines = [
            f"标题: {self.title}",
            f"公司: {self.company}",
            f"岗位: {self.position}",
            f"轮次: {self.round}",
            f"面试日期: {self.interview_date}",
            f"结果: {self.result}",
            f"背景: {self.background}",
            "",
            "面试题:",
            "\n".join(f"- {question}" for question in self.questions),
            "",
            self.answer,
            self.content,
        ]
        metadata = {
            "source": self.url,
            "doc_type": "interview_experience",
            "source_site": self.source,
            "title": self.title,
            "company": self.company,
            "position": self.position,
            "round": self.round,
            "interview_date": self.interview_date,
            "result": self.result,
            "url": self.url,
        }
        doc_id = f"interview:{self.source}:{self.url or self.title}"
        return ScrapedDocument(id=doc_id, text=normalize_space("\n".join(lines)), metadata=metadata)


class InterviewScraperBase(BaseScraper):
    """Shared scraper flow for interview experience pages."""

    source_name = "interview"
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
            for item in self.parse_experiences(text, base_url=url):
                if not item.url:
                    item = _merge_experience(item, url=url)
                if not self.should_collect(item.url, updated_at=item.interview_date):
                    report.skipped_duplicates += 1
                    continue
                report.documents.append(item.to_document())
                self.mark_seen_url(item.url, updated_at=item.interview_date)
                if item.interview_date and (newest is None or item.interview_date > newest):
                    newest = item.interview_date
        if newest:
            self.set_high_watermark(newest)
        return report

    def parse_experiences(self, text: str, *, base_url: str = "") -> list[InterviewExperience]:
        records = self._parse_json(text, base_url)
        if records:
            return records
        return self._parse_html(text, base_url)

    def _parse_json(self, text: str, base_url: str) -> list[InterviewExperience]:
        records: list[InterviewExperience] = []
        for payload in extract_json_payloads(text):
            for item in find_json_objects(
                payload,
                {"company", "position", "round", "questions", "interviewDate", "content"},
            ):
                experience = _experience_from_mapping(item, base_url, self.source_name)
                if experience.title or experience.company:
                    records.append(experience)
        return _dedupe(records)

    def _parse_html(self, text: str, base_url: str) -> list[InterviewExperience]:
        block_re = re.compile(
            r"<(?P<tag>article|div|li)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
            re.IGNORECASE | re.DOTALL,
        )
        records: list[InterviewExperience] = []
        for match in block_re.finditer(text):
            attrs = parse_attrs(match.group("attrs"))
            marker = f"{attrs.get('class', '')} {attrs.get('data-type', '')}"
            if "interview-card" not in marker and "interview" not in marker:
                continue
            record = _experience_from_attrs(attrs, match.group("body"), base_url, self.source_name)
            if record.title or record.company:
                records.append(record)
        if records:
            return _dedupe(records)
        fallback = _experience_from_attrs({}, text, base_url, self.source_name)
        return [fallback] if fallback.title or fallback.content else []


class NowcoderScraper(InterviewScraperBase):
    """Nowcoder interview-experience scraper."""

    source_name = "nowcoder"
    search_url = "https://www.nowcoder.com/search/all"


class LeetcodeScraper(InterviewScraperBase):
    """LeetCode discussion scraper for interview experiences."""

    source_name = "leetcode"
    search_url = "https://leetcode.cn/discuss/interview-experience"


def _first(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return normalize_space(str(value))
    return ""


def _as_questions(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_space(str(item)) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        parts = re.split(r"\n+|[;；]", value)
        return [normalize_space(part) for part in parts if part.strip()]
    return []


def _experience_from_mapping(
    item: Mapping[str, Any],
    base_url: str,
    source: str,
) -> InterviewExperience:
    raw_url = _first(item, "url", "href", "link", "postUrl")
    questions = _as_questions(item.get("questions") or item.get("question") or item.get("topics"))
    content = _first(item, "content", "body", "description", "summary")
    if not questions:
        questions = _extract_questions(content)
    return InterviewExperience(
        title=_first(item, "title", "subject", "name"),
        company=_first(item, "company", "companyName"),
        position=_first(item, "position", "job", "role"),
        round=_first(item, "round", "interviewRound"),
        questions=questions,
        answer=_first(item, "answer", "solution", "thinking"),
        background=_first(item, "background", "candidateBackground"),
        result=_first(item, "result", "status", "outcome"),
        interview_date=_first(item, "interviewDate", "interview_date", "date", "publishedAt"),
        content=content,
        url=urljoin(base_url, raw_url) if raw_url else base_url,
        source=source,
    )


def _experience_from_attrs(
    attrs: Mapping[str, str],
    body: str,
    base_url: str,
    source: str,
) -> InterviewExperience:
    anchor = re.search(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", body, re.IGNORECASE | re.DOTALL)
    anchor_attrs = parse_attrs(anchor.group("attrs")) if anchor else {}
    content = _pick_by_class(body, "content", "description", "body") or strip_html(body)
    return InterviewExperience(
        title=_pick_attr(attrs, "data-title") or (strip_html(anchor.group("body")) if anchor else ""),
        company=_pick_attr(attrs, "data-company") or _pick_by_class(body, "company"),
        position=_pick_attr(attrs, "data-position") or _pick_by_class(body, "position"),
        round=_pick_attr(attrs, "data-round") or _pick_by_class(body, "round"),
        questions=_extract_questions(content),
        answer=_pick_by_class(body, "answer", "solution"),
        background=_pick_by_class(body, "background"),
        result=_pick_attr(attrs, "data-result") or _pick_by_class(body, "result"),
        interview_date=_pick_attr(attrs, "data-date") or _pick_by_class(body, "date"),
        content=content,
        url=urljoin(base_url, anchor_attrs.get("href", attrs.get("data-url", ""))) or base_url,
        source=source,
    )


def _extract_questions(text: str) -> list[str]:
    questions: list[str] = []
    for part in re.split(r"\n+|[;；]", text):
        cleaned = normalize_space(part)
        if not cleaned:
            continue
        if "?" in cleaned or "？" in cleaned or cleaned.startswith(("问", "题")):
            questions.append(cleaned)
    return questions[:20]


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


def _merge_experience(seed: InterviewExperience, **updates: str) -> InterviewExperience:
    values = seed.__dict__.copy()
    for key, value in updates.items():
        if value:
            values[key] = value
    return InterviewExperience(**values)


def _dedupe(records: list[InterviewExperience]) -> list[InterviewExperience]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[InterviewExperience] = []
    for record in records:
        key = (record.url, record.title, record.company)
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique
