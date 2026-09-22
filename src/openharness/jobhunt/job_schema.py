"""安全岗位数据模型与外部内容边界。

岗位数据来自外部服务，不能直接进入本地岗位库或模型上下文。这个模块
负责来源注册、URL 校验、内容清洗和统一岗位记录，供 MCP/API 适配层复用。
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openharness.rag.utils import content_hash

SAFE_JOB_TOOL_NAMES = frozenset({"list_sources", "search_jobs", "get_job_details"})
BLOCKED_JOB_TOOL_NAMES = frozenset(
    {
        "login",
        "logout",
        "refresh_session",
        "submit_application",
        "apply_job",
        "send_message",
        "upload_resume",
        "browser_control",
        "fetch_url",
        "execute_command",
        "read_file",
        "write_file",
    }
)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"[ \t]+")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "password",
    "passwd",
    "refresh_token",
    "session",
    "token",
}


class JobDataError(ValueError):
    """Base error for rejected or unsafe external job data."""


class SourceValidationError(JobDataError):
    """Raised when a source or URL is not trusted."""


class JobSchemaError(JobDataError):
    """Raised when a provider record does not satisfy the job contract."""


class _SafeHtmlParser(HTMLParser):
    """Extract visible text while dropping active/hidden HTML content."""

    _DROP_TAGS = frozenset(
        {"script", "style", "noscript", "template", "iframe", "object", "embed", "form"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in self._DROP_TAGS:
            self._drop_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() not in self._DROP_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._DROP_TAGS and self._drop_depth:
            self._drop_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.parts.append(data)


def sanitize_external_text(value: Any, *, max_chars: int) -> str:
    """Return bounded plain text suitable for storage and model retrieval."""

    text = "" if value is None else str(value)
    if "<" in text and ">" in text:
        parser = _SafeHtmlParser()
        try:
            parser.feed(text)
            parser.close()
            text = " ".join(parser.parts)
        except Exception:  # noqa: BLE001
            text = re.sub(r"<[^>]*>", " ", text)
    text = html.unescape(text)
    text = _CONTROL_CHARS_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:max(0, max_chars)]


def utc_now_iso() -> str:
    """Return a stable UTC timestamp for job provenance."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def _hostname_is_private(hostname: str) -> bool:
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        return True
    if lowered.endswith((".local", ".localhost")):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def _query_has_sensitive_key(query: str) -> bool:
    for pair in query.split("&"):
        key = pair.split("=", 1)[0].strip().lower()
        if key in _SENSITIVE_QUERY_KEYS:
            return True
    return False


def canonicalize_https_url(
    raw_url: str,
    *,
    allowed_domains: Iterable[str],
    field_name: str = "source_url",
) -> str:
    """Validate and canonicalize a source link against an approved domain set."""

    value = sanitize_external_text(raw_url, max_chars=2048)
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise SourceValidationError(f"{field_name} must use https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise SourceValidationError(f"{field_name} contains an invalid host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceValidationError(f"{field_name} contains an invalid port") from exc
    if port not in (None, 443):
        raise SourceValidationError(f"{field_name} must use the default HTTPS port")
    hostname = parsed.hostname.lower().rstrip(".")
    if _hostname_is_private(hostname):
        raise SourceValidationError(f"{field_name} points to a private or local address")
    domains = {str(item).strip().lower().rstrip(".") for item in allowed_domains if str(item).strip()}
    if not domains or not any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
        raise SourceValidationError(f"{field_name} domain is not approved")
    if _query_has_sensitive_key(parsed.query):
        raise SourceValidationError(f"{field_name} contains an authentication parameter")
    path = parsed.path or "/"
    return urlunsplit(("https", hostname, path, parsed.query, ""))


@dataclass(frozen=True)
class SourceRegistration:
    """Approved source metadata used to validate provider-returned links."""

    source_code: str
    source_site: str
    allowed_domains: tuple[str, ...]
    default_company: str = ""
    company_id: str = ""
    official_career_url: str = ""
    aliases: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        code = self.source_code.strip()
        site = self.source_site.strip()
        domains = tuple(
            sorted({str(domain).strip().lower().rstrip(".") for domain in self.allowed_domains if str(domain).strip()})
        )
        if not code or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", code):
            raise SourceValidationError("source_code must be a short stable identifier")
        if not site or not domains:
            raise SourceValidationError("source registration requires a site and domain")
        object.__setattr__(self, "source_code", code)
        object.__setattr__(self, "source_site", site[:120])
        object.__setattr__(self, "allowed_domains", domains)
        object.__setattr__(self, "default_company", sanitize_external_text(self.default_company, max_chars=200))
        object.__setattr__(self, "company_id", sanitize_external_text(self.company_id, max_chars=120))
        career_url = self.official_career_url.strip()
        object.__setattr__(
            self,
            "official_career_url",
            canonicalize_https_url(career_url, allowed_domains=domains, field_name="official_career_url")
            if career_url
            else "",
        )
        object.__setattr__(
            self,
            "aliases",
            tuple(
                dict.fromkeys(
                    sanitize_external_text(item, max_chars=200)
                    for item in self.aliases
                    if sanitize_external_text(item, max_chars=200)
                )
            ),
        )
        object.__setattr__(
            self,
            "departments",
            tuple(
                dict.fromkeys(
                    sanitize_external_text(item, max_chars=120)
                    for item in self.departments
                    if sanitize_external_text(item, max_chars=120)
                )
            ),
        )

    def validate_url(self, url: str, *, field_name: str = "source_url") -> str:
        return canonicalize_https_url(url, allowed_domains=self.allowed_domains, field_name=field_name)


class SourceRegistry:
    """In-memory allowlist for reviewed job sources."""

    def __init__(self, registrations: Iterable[SourceRegistration] = ()) -> None:
        self._items = {item.source_code: item for item in registrations}

    @classmethod
    def from_mappings(cls, values: Iterable[Mapping[str, Any]]) -> SourceRegistry:
        registrations: list[SourceRegistration] = []
        for value in values:
            domains = value.get("allowed_domains") or value.get("domains") or ()
            if isinstance(domains, str):
                domains = [domains]
            aliases = value.get("aliases") or ()
            if isinstance(aliases, str):
                aliases = [aliases]
            departments = value.get("departments") or ()
            if isinstance(departments, str):
                departments = [departments]
            registrations.append(
                SourceRegistration(
                    source_code=str(value.get("source_code") or value.get("code") or ""),
                    source_site=str(value.get("source_site") or value.get("site") or ""),
                    allowed_domains=tuple(str(domain) for domain in domains),
                    default_company=str(value.get("default_company") or ""),
                    company_id=str(value.get("company_id") or ""),
                    official_career_url=str(value.get("official_career_url") or ""),
                    aliases=tuple(str(item) for item in aliases),
                    departments=tuple(str(item) for item in departments),
                )
            )
        return cls(registrations)

    def get(self, source_code: str) -> SourceRegistration | None:
        return self._items.get(str(source_code).strip())

    def require(self, source_code: str) -> SourceRegistration:
        registration = self.get(source_code)
        if registration is None:
            raise SourceValidationError(f"source_code is not registered: {source_code!r}")
        return registration

    def registrations(self) -> tuple[SourceRegistration, ...]:
        """Return registrations in stable source-code order."""

        return tuple(self._items[code] for code in sorted(self._items))

    def validate_provider_sources(self, sources: Iterable[Mapping[str, Any]]) -> None:
        """Reject a provider that advertises an unregistered source."""

        for source in sources:
            code = str(source.get("source_code") or source.get("code") or "").strip()
            registration = self.require(code)
            advertised_domains = source.get("domains") or source.get("allowed_domains") or ()
            if isinstance(advertised_domains, str):
                advertised_domains = [advertised_domains]
            for domain in advertised_domains:
                normalized = str(domain).strip().lower().rstrip(".")
                if normalized and not any(
                    normalized == approved or normalized.endswith(f".{approved}")
                    for approved in registration.allowed_domains
                ):
                    raise SourceValidationError(f"provider domain is not approved for {code}")

    def __bool__(self) -> bool:
        return bool(self._items)


def _first(raw: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return value
    return ""


def _optional_int(raw: Mapping[str, Any], *keys: str) -> int | None:
    value = _first(raw, *keys)
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class JobRecord:
    """Canonical job record shared by JSON cache, RAG and API responses."""

    id: str
    title: str
    company: str
    city: str
    salary: str
    experience: str
    education: str
    description: str
    source_code: str
    source_site: str
    source_url: str
    apply_url: str
    provider: str
    provider_record_id: str
    published_at: str
    fetched_at: str
    last_seen_at: str
    provenance_status: str = "verified"
    status: str = "active"
    content_hash: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    tags: list[str] = field(default_factory=list)
    company_size: str = ""
    industry: str = ""
    financing: str = ""
    department: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "salary": self.salary,
            "experience": self.experience,
            "education": self.education,
            "description": self.description,
            "source_code": self.source_code,
            "source_site": self.source_site,
            "source_url": self.source_url,
            "apply_url": self.apply_url,
            "provider": self.provider,
            "provider_record_id": self.provider_record_id,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "last_seen_at": self.last_seen_at,
            "provenance_status": self.provenance_status,
            "status": self.status,
            "content_hash": self.content_hash,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "tags": list(self.tags),
            "company_size": self.company_size,
            "industry": self.industry,
            "financing": self.financing,
            "department": self.department,
        }

    def to_rag_text(self) -> str:
        return "\n".join(
            (
                f"岗位: {self.title}",
                f"公司: {self.company}",
                f"城市: {self.city}",
                f"薪资: {self.salary}",
                f"经验: {self.experience}",
                f"学历: {self.education}",
                f"发布时间: {self.published_at}",
                "",
                self.description,
            )
        ).strip()

    def to_rag_metadata(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "doc_type": "job_posting",
            "external_content": True,
            "source_code": self.source_code,
            "source_site": self.source_site,
            "source_url": self.source_url,
            "apply_url": self.apply_url,
            "provider": self.provider,
            "provider_record_id": self.provider_record_id,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "last_seen_at": self.last_seen_at,
            "provenance_status": self.provenance_status,
            "status": self.status,
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "salary": self.salary,
            "salary_min": self.salary_min or 0,
            "salary_max": self.salary_max or 0,
            "experience": self.experience,
            "education": self.education,
        }


def normalize_job_record(
    raw: Mapping[str, Any],
    *,
    provider: str,
    registry: SourceRegistry,
    fetched_at: str | None = None,
) -> JobRecord:
    """Validate, sanitize and normalize one provider record."""

    if not isinstance(raw, Mapping):
        raise JobSchemaError("job record must be an object")
    source_code = sanitize_external_text(
        _first(raw, "source_code", "sourceCode", "source"), max_chars=64
    )
    registration = registry.require(source_code)
    provider_name = sanitize_external_text(provider, max_chars=64)
    record_id = sanitize_external_text(
        _first(raw, "provider_record_id", "providerRecordId", "job_id", "jobId", "id"),
        max_chars=160,
    )
    if not provider_name or not record_id:
        raise JobSchemaError("provider and provider_record_id are required")

    source_url_raw = sanitize_external_text(
        _first(raw, "source_url", "sourceUrl", "url", "job_url", "jobUrl"), max_chars=2048
    )
    source_url = registration.validate_url(source_url_raw)
    apply_url_raw = sanitize_external_text(
        _first(raw, "apply_url", "applyUrl", "application_url", "applicationUrl"), max_chars=2048
    )
    apply_url = registration.validate_url(apply_url_raw or source_url, field_name="apply_url")
    now = fetched_at or utc_now_iso()
    title = sanitize_external_text(_first(raw, "title", "jobName", "positionName", "name"), max_chars=200)
    company = sanitize_external_text(
        _first(raw, "company", "companyName", "brandName") or registration.default_company,
        max_chars=200,
    )
    if not title or not company:
        raise JobSchemaError("title and company are required")
    description = sanitize_external_text(
        _first(raw, "description", "jobDescription", "detail", "content"), max_chars=50_000
    )
    tags_value = raw.get("tags") or raw.get("jobLabels") or ()
    if isinstance(tags_value, str):
        tags_value = [tags_value]
    tags = [
        sanitize_external_text(item, max_chars=64)
        for item in tags_value
        if sanitize_external_text(item, max_chars=64)
    ][:20]
    seed = f"{title}\n{company}\n{description}\n{source_url}"
    safe_record_id = record_id if _SAFE_ID_RE.fullmatch(record_id) else content_hash(record_id, length=20)
    return JobRecord(
        id=f"job:{provider_name}:{safe_record_id}",
        title=title,
        company=company,
        city=sanitize_external_text(_first(raw, "city", "cityName", "workCity"), max_chars=100),
        salary=sanitize_external_text(
            _first(raw, "salary", "salaryDesc", "salaryRange"), max_chars=100
        ),
        experience=sanitize_external_text(
            _first(raw, "experience", "experienceName", "workYear", "jobExperience"),
            max_chars=100,
        ),
        education=sanitize_external_text(
            _first(raw, "education", "degreeName", "educationName"), max_chars=100
        ),
        description=description,
        source_code=registration.source_code,
        source_site=registration.source_site,
        source_url=source_url,
        apply_url=apply_url,
        provider=provider_name,
        provider_record_id=record_id,
        published_at=sanitize_external_text(
            _first(raw, "published_at", "publishedAt", "publishTime", "updateTime", "createTime"),
            max_chars=64,
        ),
        fetched_at=now,
        last_seen_at=now,
        content_hash=content_hash(seed, length=32),
        salary_min=_optional_int(raw, "salary_min", "salaryMin"),
        salary_max=_optional_int(raw, "salary_max", "salaryMax"),
        tags=tags,
        company_size=sanitize_external_text(
            _first(raw, "company_size", "companySize", "scaleName", "staffSize"), max_chars=100
        ),
        industry=sanitize_external_text(
            _first(raw, "industry", "industryName", "industryField"), max_chars=100
        ),
        financing=sanitize_external_text(
            _first(raw, "financing", "financeStage", "financeStageName"), max_chars=100
        ),
        department=sanitize_external_text(
            _first(raw, "department", "departmentName", "bg_name", "bgName", "BGName"),
            max_chars=120,
        ),
    )


def parse_json_payload(value: Any, *, max_bytes: int) -> Any:
    """Decode a provider response while enforcing a byte budget."""

    if isinstance(value, (dict, list)):
        payload = value
    elif isinstance(value, str):
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) > max_bytes:
            raise JobDataError("provider response exceeds the configured byte limit")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise JobDataError("provider returned invalid JSON") from exc
    else:
        raise JobDataError("provider returned an unsupported payload")
    encoded = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) > max_bytes:
        raise JobDataError("provider response exceeds the configured byte limit")
    return payload
