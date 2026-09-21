"""Read-only job provider adapters and local-first synchronization.

The provider boundary intentionally accepts only structured job operations.
It never accepts a recruitment-site password, cookie, arbitrary URL, browser
command or resume text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

import httpx

from openharness.config.schema import ScrapingSettings
from openharness.jobhunt.job_schema import (
    BLOCKED_JOB_TOOL_NAMES,
    SAFE_JOB_TOOL_NAMES,
    JobDataError,
    JobRecord,
    JobSchemaError,
    SourceRegistry,
    SourceValidationError,
    normalize_job_record,
    parse_json_payload,
    sanitize_external_text,
)

MAX_QUERY_CHARS = 200
MAX_FILTER_CHARS = 100
DEFAULT_FRESHNESS_HOURS = 24
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_SENSITIVE_INPUT_RE = re.compile(
    r"(?:cookie|authorization|bearer|password|passwd|refresh[_ -]?token|session[_ -]?id)"
    r"\s*[:=]",
    re.IGNORECASE,
)
_SENSITIVE_ERROR_RE = re.compile(
    r"(?i)(cookie|authorization|bearer|password|passwd|refresh[_ -]?token|token|session[_ -]?id)"
    r"\s*[:=]\s*[^,\s;]+"
)
_JOB_BLOCK_RE = re.compile(
    r"<(?P<tag>article|li|div)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", re.DOTALL)
_CLASS_TEXT_RE = re.compile(
    r"<[^>]*class=['\"][^'\"]*(?P<class>{class_name})[^'\"]*['\"][^>]*>(?P<body>.*?)</[^>]+>",
    re.IGNORECASE | re.DOTALL,
)


class JobProviderError(RuntimeError):
    """Raised when a trusted provider cannot complete a read-only request."""


class UnsafeJobProviderError(JobProviderError):
    """Raised when provider configuration violates the job safety boundary."""


class _VisibleTextParser(HTMLParser):
    """Small dependency-free text extractor for public job pages."""

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
        elif tag.lower() in {"br", "p", "li", "div", "section", "article"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._DROP_TAGS and self._drop_depth:
            self._drop_depth -= 1
        elif tag.lower() in {"p", "li", "div", "section", "article"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.parts.append(data)


def _parse_attrs(tag_text: str) -> dict[str, str]:
    return {match.group(1).lower(): match.group(3) for match in _ATTR_RE.finditer(tag_text)}


def _visible_text(markup: str, *, max_chars: int = 50_000) -> str:
    parser = _VisibleTextParser()
    try:
        parser.feed(markup)
        parser.close()
        text = " ".join(parser.parts)
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", markup)
    return sanitize_external_text(text, max_chars=max_chars)


def _pick_attr(attrs: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = attrs.get(key)
        if value:
            return sanitize_external_text(value, max_chars=500)
    return ""


def _pick_by_class(markup: str, *class_names: str, max_chars: int = 500) -> str:
    for class_name in class_names:
        pattern = re.compile(
            _CLASS_TEXT_RE.pattern.format(class_name=re.escape(class_name)),
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(markup)
        if match:
            return _visible_text(match.group("body"), max_chars=max_chars)
    return ""


def _find_json_objects(value: Any, required_keys: set[str]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        lowered = {str(key).lower() for key in value}
        if lowered.intersection(required_keys):
            found.append(value)
        for child in value.values():
            found.extend(_find_json_objects(child, required_keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_json_objects(child, required_keys))
    return found


def _extract_json_payloads(text: str, *, max_bytes: int) -> list[Any]:
    payloads: list[Any] = []
    stripped = text.strip()
    if not stripped:
        return payloads
    try:
        payloads.append(parse_json_payload(stripped, max_bytes=max_bytes))
        return payloads
    except JobDataError:
        pass
    script_re = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    for match in script_re.finditer(text):
        body = match.group(1).strip()
        for candidate in (body, _extract_assignment_json(body)):
            if not candidate:
                continue
            try:
                payloads.append(parse_json_payload(candidate, max_bytes=max_bytes))
                break
            except JobDataError:
                continue
    return payloads


def _extract_assignment_json(script_body: str) -> str:
    starts = [pos for pos in (script_body.find("{"), script_body.find("[")) if pos >= 0]
    if not starts:
        return ""
    return script_body[min(starts) :].rstrip(";")


@dataclass(frozen=True)
class JobSyncLimits:
    """Hard resource limits enforced by OpenHarness."""

    max_results: int = 50
    max_pages: int = 1
    max_details: int = 50
    max_response_bytes: int = 2_000_000
    max_retries: int = 1
    max_concurrency: int = 1
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS

    def __post_init__(self) -> None:
        if self.max_results < 1 or self.max_results > 100:
            raise ValueError("max_results must be between 1 and 100")
        if self.max_pages != 1:
            raise ValueError("job synchronization only supports one page")
        if self.max_details < 0 or self.max_details > self.max_results:
            raise ValueError("max_details must be between 0 and max_results")
        if self.max_response_bytes < 1024:
            raise ValueError("max_response_bytes is too small")
        if self.max_retries < 0 or self.max_retries > 2:
            raise ValueError("max_retries must be between 0 and 2")
        if self.max_concurrency != 1:
            raise ValueError("job synchronization concurrency must remain 1")
        if self.freshness_hours < 1:
            raise ValueError("freshness_hours must be positive")


@dataclass(frozen=True)
class JobSearchQuery:
    """Minimal query sent to an external job provider."""

    query: str = ""
    city: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    experience: str = ""
    education: str = ""
    limit: int = 10

    def __post_init__(self) -> None:
        for name, value in (
            ("query", self.query),
            ("city", self.city),
            ("experience", self.experience),
            ("education", self.education),
        ):
            if len(str(value)) > (MAX_QUERY_CHARS if name == "query" else MAX_FILTER_CHARS):
                raise ValueError(f"{name} exceeds the configured length limit")
            if _EMAIL_RE.search(str(value)) or _PHONE_RE.search(str(value)) or _SENSITIVE_INPUT_RE.search(
                str(value)
            ):
                raise ValueError(f"{name} contains sensitive data that cannot be sent to a job provider")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if self.salary_min is not None and self.salary_min < 0:
            raise ValueError("salary_min must be non-negative")
        if self.salary_max is not None and self.salary_max < 0:
            raise ValueError("salary_max must be non-negative")
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salary_min cannot exceed salary_max")

    def to_provider_args(self, *, max_results: int) -> dict[str, Any]:
        """Return only the fields needed for job search."""

        payload: dict[str, Any] = {
            "query": self.query[:MAX_QUERY_CHARS],
            "limit": min(self.limit, max_results),
            "page": 1,
        }
        for key in ("city", "experience", "education"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        if self.salary_min is not None:
            payload["salary_min"] = self.salary_min
        if self.salary_max is not None:
            payload["salary_max"] = self.salary_max
        return payload


class ReadOnlyJobsProvider(Protocol):
    """Provider contract limited to the three reviewed Jobs operations."""

    provider_name: str

    def list_sources(self) -> list[Mapping[str, Any]]:
        ...

    def search_jobs(self, query: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        ...

    def get_job_details(
        self,
        provider_record_id: str,
        *,
        source_url: str,
    ) -> Mapping[str, Any]:
        ...


def _payload_items(payload: Any, *keys: str) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        raise JobDataError("provider payload must be an object or array")
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, Mapping)]
    if all(key in payload for key in ("title", "company")):
        return [payload]
    raise JobDataError("provider payload does not contain job records")


def _payload_object(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        candidate = payload.get("job") or payload.get("result") or payload
        if isinstance(candidate, Mapping):
            return candidate
    raise JobDataError("provider detail payload must be an object")


class McpJobsProvider:
    """Adapter for a reviewed, read-only Jobs MCP server."""

    def __init__(
        self,
        manager: Any,
        *,
        server_name: str,
        provider_name: str | None = None,
        tool_map: Mapping[str, str] | None = None,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        self._manager = manager
        self.server_name = server_name.strip()
        self.provider_name = (provider_name or self.server_name).strip()
        supplied_tool_map = dict(tool_map or {})
        unexpected_operations = set(supplied_tool_map) - set(SAFE_JOB_TOOL_NAMES)
        if unexpected_operations:
            raise UnsafeJobProviderError(
                "tool mapping contains non-job operations: "
                + ", ".join(sorted(unexpected_operations))
            )
        self._tool_map = {
            operation: str((tool_map or {}).get(operation, operation)).strip()
            for operation in SAFE_JOB_TOOL_NAMES
        }
        self._max_response_bytes = max_response_bytes
        self._validate_configuration()

    def _validate_configuration(self) -> None:
        if not self.server_name or not self.provider_name:
            raise UnsafeJobProviderError("a provider server and provider name are required")
        for operation, tool_name in self._tool_map.items():
            if not tool_name or tool_name in BLOCKED_JOB_TOOL_NAMES:
                raise UnsafeJobProviderError(f"unsafe MCP tool mapping for {operation}")
        config = self._manager.get_server_config(self.server_name)
        if config is None:
            raise UnsafeJobProviderError(f"provider server is not configured: {self.server_name}")
        if getattr(config, "type", "") == "http":
            url = str(getattr(config, "url", ""))
            parsed = urlsplit(url)
            if parsed.scheme.lower() != "https":
                raise UnsafeJobProviderError("remote Jobs MCP must use HTTPS")
        if getattr(config, "type", "") == "ws":
            raise UnsafeJobProviderError("WebSocket Jobs MCP is not enabled")
        headers = getattr(config, "headers", {}) or {}
        if any(str(key).lower() == "cookie" for key in headers):
            raise UnsafeJobProviderError("recruitment-site cookies are forbidden")

    def _call(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        if operation not in SAFE_JOB_TOOL_NAMES:
            raise UnsafeJobProviderError(f"operation is not allowlisted: {operation}")

        async def invoke() -> str:
            # The CLI uses a short-lived manager. Connecting per operation keeps
            # the synchronous adapter from leaking an event loop across calls.
            connect_all = getattr(self._manager, "connect_all", None)
            close = getattr(self._manager, "close", None)
            if connect_all is not None:
                await connect_all()
            try:
                self._validate_connected_tools()
                return await self._manager.call_tool(
                    self.server_name,
                    self._tool_map[operation],
                    dict(arguments),
                )
            finally:
                if close is not None:
                    await close()

        try:
            output = asyncio.run(invoke())
        except Exception as exc:
            if isinstance(exc, (UnsafeJobProviderError, JobProviderError)):
                raise
            raise JobProviderError(f"Jobs MCP {operation} failed: {exc.__class__.__name__}") from exc
        return parse_json_payload(output, max_bytes=self._max_response_bytes)

    def _validate_connected_tools(self) -> None:
        """Require the connected server to expose only an approved job surface."""

        list_statuses = getattr(self._manager, "list_statuses", None)
        if list_statuses is None:
            raise UnsafeJobProviderError("Jobs MCP client cannot verify its tool allowlist")
        statuses = list_statuses()
        status = next(
            (item for item in statuses if str(getattr(item, "name", "")) == self.server_name),
            None,
        )
        if status is None or str(getattr(status, "state", "")) != "connected":
            detail = str(getattr(status, "detail", "") or "server is not connected")
            raise JobProviderError(f"Jobs MCP server is unavailable: {detail[:200]}")
        declared = {
            str(getattr(tool, "name", "")).strip()
            for tool in (getattr(status, "tools", None) or [])
            if str(getattr(tool, "name", "")).strip()
        }
        blocked = declared & BLOCKED_JOB_TOOL_NAMES
        if blocked:
            raise UnsafeJobProviderError(
                "Jobs MCP declared blocked tools: " + ", ".join(sorted(blocked))
            )
        missing = set(self._tool_map.values()) - declared
        if missing:
            raise UnsafeJobProviderError(
                "Jobs MCP is missing mapped read-only tools: " + ", ".join(sorted(missing))
            )

    def list_sources(self) -> list[Mapping[str, Any]]:
        payload = self._call("list_sources", {})
        return _payload_items(payload, "sources", "items")

    def search_jobs(self, query: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        return _payload_items(self._call("search_jobs", query), "jobs", "items", "results")

    def get_job_details(
        self,
        provider_record_id: str,
        *,
        source_url: str,
    ) -> Mapping[str, Any]:
        payload = self._call(
            "get_job_details",
            {"provider_record_id": provider_record_id, "source_url": source_url},
        )
        return _payload_object(payload)


class SafePublicJobsProvider:
    """Built-in public job fetcher with a strict account-safe boundary.

    This provider reads only configured HTTPS sources. It does not accept
    cookies, Authorization headers, browser sessions, arbitrary URLs or resume
    text. Sources are expected to be reviewed and registered in
    ``scraping.allowed_sources`` with either ``search_url_template`` or
    ``feed_url``.
    """

    provider_name = "openharness_public_jobs"

    def __init__(
        self,
        sources: Sequence[Mapping[str, Any]],
        *,
        registry: SourceRegistry,
        max_response_bytes: int = 2_000_000,
        timeout_seconds: float = 30.0,
        request_delay_min: float = 2.0,
        request_delay_max: float = 5.0,
        respect_robots_txt: bool = True,
        user_agent: str = "OpenHarnessJobFetcher/1.0",
        client: httpx.Client | None = None,
        sleeper: Any = time.sleep,
    ) -> None:
        self._registry = registry
        self._sources = [dict(source) for source in sources if isinstance(source, Mapping)]
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._request_delay_min = max(0.0, request_delay_min)
        self._request_delay_max = max(self._request_delay_min, request_delay_max)
        self._respect_robots_txt = respect_robots_txt
        self._user_agent = user_agent
        self._client = client
        self._owns_client = client is None
        self._sleeper = sleeper
        self._last_request_at: float | None = None
        self._robots: dict[str, bool] = {}
        self._validate_configuration()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _validate_configuration(self) -> None:
        if not self._registry:
            raise UnsafeJobProviderError("public job fetching requires allowed_sources")
        if not self._sources:
            raise UnsafeJobProviderError("public job fetching requires at least one source")
        configured_sources = 0
        for source in self._sources:
            code = str(source.get("source_code") or source.get("code") or "").strip()
            registration = self._registry.require(code)
            template = str(source.get("search_url_template") or source.get("feed_url") or "").strip()
            if not template:
                continue
            configured_sources += 1
            if any(token in template.lower() for token in ("{cookie", "{authorization", "{password", "{token")):
                raise UnsafeJobProviderError(f"source {code} has an unsafe URL template")
            probe = self._format_url(
                template,
                query="engineer",
                city="",
                limit=1,
                page=1,
            )
            registration.validate_url(probe)
        if configured_sources == 0:
            raise UnsafeJobProviderError(
                "public job fetching requires search_url_template or feed_url"
            )

    def list_sources(self) -> list[Mapping[str, Any]]:
        items: list[Mapping[str, Any]] = []
        for source in self._sources:
            code = str(source.get("source_code") or source.get("code") or "").strip()
            registration = self._registry.require(code)
            if not (source.get("search_url_template") or source.get("feed_url")):
                continue
            items.append(
                {
                    "source_code": registration.source_code,
                    "source_site": registration.source_site,
                    "domains": list(registration.allowed_domains),
                }
            )
        return items

    def search_jobs(self, query: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        args = dict(query)
        limit = max(1, min(int(args.get("limit") or 10), 100))
        results: list[Mapping[str, Any]] = []
        for source in self._sources:
            if len(results) >= limit:
                break
            code = str(source.get("source_code") or source.get("code") or "").strip()
            registration = self._registry.require(code)
            template = str(source.get("search_url_template") or source.get("feed_url") or "").strip()
            if not template:
                continue
            url = self._format_url(
                template,
                query=str(args.get("query") or ""),
                city=str(args.get("city") or ""),
                limit=limit - len(results),
                page=int(args.get("page") or 1),
            )
            safe_url = registration.validate_url(url)
            text = self._fetch(safe_url, registration=registration)
            parsed = self._parse_jobs(text, source=source, base_url=safe_url)
            results.extend(parsed[: limit - len(results)])
        return results[:limit]

    def get_job_details(
        self,
        provider_record_id: str,
        *,
        source_url: str,
    ) -> Mapping[str, Any]:
        source = self._source_for_url(source_url)
        code = str(source.get("source_code") or source.get("code") or "").strip()
        registration = self._registry.require(code)
        safe_url = registration.validate_url(source_url)
        text = self._fetch(safe_url, registration=registration)
        jobs = self._parse_jobs(text, source=source, base_url=safe_url)
        detail = dict(jobs[0]) if jobs else {}
        detail.setdefault("provider_record_id", provider_record_id)
        detail.setdefault("source_url", safe_url)
        detail.setdefault("apply_url", safe_url)
        detail.setdefault("description", _visible_text(text))
        return detail

    @staticmethod
    def _format_url(template: str, *, query: str, city: str, limit: int, page: int) -> str:
        values = {
            "query": quote_plus(query),
            "city": quote_plus(city),
            "limit": str(limit),
            "page": str(page),
        }
        try:
            return template.format(**values)
        except KeyError as exc:
            raise UnsafeJobProviderError(f"unsupported public job URL template field: {exc}") from exc

    def _client_for_request(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout_seconds, follow_redirects=True)
        return self._client

    def _fetch(self, url: str, *, registration: Any) -> str:
        if self._respect_robots_txt and not self._robots_allows(url, registration=registration):
            raise JobProviderError("robots.txt disallows the configured job source")
        self._rate_limit()
        try:
            response = self._client_for_request().get(
                url,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                },
                follow_redirects=True,
            )
            final_url = registration.validate_url(str(response.url))
            del final_url
            if len(response.content) > self._max_response_bytes:
                raise JobDataError("provider response exceeds the configured byte limit")
            response.raise_for_status()
            return response.text
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise JobProviderError("public job source request failed") from exc

    def _robots_allows(self, url: str, *, registration: Any) -> bool:
        parsed = urlsplit(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root in self._robots:
            return self._robots[root]
        robots_url = registration.validate_url(urljoin(root, "/robots.txt"))
        allowed = True
        try:
            self._rate_limit()
            response = self._client_for_request().get(
                robots_url,
                headers={"User-Agent": self._user_agent},
                follow_redirects=True,
            )
            registration.validate_url(str(response.url))
            if response.status_code < 400 and "disallow: /" in response.text.lower():
                path = parsed.path or "/"
                allowed = not any(
                    line.strip().lower() == "disallow: /" or line.strip().lower() == f"disallow: {path.lower()}"
                    for line in response.text.splitlines()
                )
        except Exception:  # noqa: BLE001
            allowed = True
        self._robots[root] = allowed
        return allowed

    def _rate_limit(self) -> None:
        if self._last_request_at is not None and self._request_delay_min > 0:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._request_delay_min:
                self._sleeper(self._request_delay_min - elapsed)
        self._last_request_at = time.monotonic()

    def _source_for_url(self, source_url: str) -> Mapping[str, Any]:
        for source in self._sources:
            code = str(source.get("source_code") or source.get("code") or "").strip()
            registration = self._registry.require(code)
            try:
                registration.validate_url(source_url)
            except SourceValidationError:
                continue
            return source
        raise SourceValidationError("source_url does not match a configured public job source")

    def _parse_jobs(
        self,
        text: str,
        *,
        source: Mapping[str, Any],
        base_url: str,
    ) -> list[Mapping[str, Any]]:
        records = self._parse_json_jobs(text, source=source, base_url=base_url)
        if records:
            return records
        return self._parse_html_jobs(text, source=source, base_url=base_url)

    def _parse_json_jobs(
        self,
        text: str,
        *,
        source: Mapping[str, Any],
        base_url: str,
    ) -> list[Mapping[str, Any]]:
        records: list[Mapping[str, Any]] = []
        required_keys = {
            "title",
            "jobname",
            "positionname",
            "company",
            "companyname",
            "brandname",
            "recruitpostname",
            "bgname",
            "salary",
            "salarydesc",
        }
        for payload in _extract_json_payloads(text, max_bytes=self._max_response_bytes):
            for item in _find_json_objects(payload, required_keys):
                record = self._job_from_mapping(item, source=source, base_url=base_url)
                if record.get("title") and record.get("company"):
                    records.append(record)
        return self._dedupe(records)

    def _parse_html_jobs(
        self,
        text: str,
        *,
        source: Mapping[str, Any],
        base_url: str,
    ) -> list[Mapping[str, Any]]:
        records: list[Mapping[str, Any]] = []
        for match in _JOB_BLOCK_RE.finditer(text):
            attrs = _parse_attrs(match.group("attrs"))
            classes = attrs.get("class", "")
            if "job" not in classes and "job" not in attrs.get("data-type", ""):
                continue
            record = self._job_from_html(attrs, match.group("body"), source=source, base_url=base_url)
            if record.get("title") and record.get("company"):
                records.append(record)
        if records:
            return self._dedupe(records)
        record = self._job_from_html({}, text, source=source, base_url=base_url)
        return [record] if record.get("title") and record.get("company") else []

    def _job_from_mapping(
        self,
        item: Mapping[str, Any],
        *,
        source: Mapping[str, Any],
        base_url: str,
    ) -> Mapping[str, Any]:
        raw_url = self._first(
            item,
            "source_url",
            "sourceUrl",
            "url",
            "jobUrl",
            "PostURL",
            "postUrl",
            "href",
            "link",
        )
        source_url = self._absolute_source_url(raw_url, source=source, base_url=base_url)
        record_id = self._first(
            item,
            "provider_record_id",
            "providerRecordId",
            "job_id",
            "jobId",
            "PostId",
            "postId",
            "id",
        )
        company = self._first(
            item,
            "company",
            "company_name",
            "companyName",
            "brandName",
            "BGName",
            "department",
        ) or sanitize_external_text(source.get("default_company"), max_chars=200)
        description = self._first(
            item,
            "description",
            "jobDescription",
            "detail",
            "content",
            "Responsibility",
            "responsibility",
            "Requirement",
            "requirement",
        )
        return {
            "provider_record_id": record_id or hashlib.sha256(source_url.encode()).hexdigest()[:24],
            "source_code": str(source.get("source_code") or source.get("code") or ""),
            "source_url": source_url,
            "apply_url": self._absolute_source_url(
                self._first(item, "apply_url", "applyUrl", "application_url", "applicationUrl") or source_url,
                source=source,
                base_url=base_url,
            ),
            "title": self._first(
                item,
                "title",
                "RecruitPostName",
                "recruitPostName",
                "jobName",
                "positionName",
                "name",
            ),
            "company": company,
            "city": self._first(
                item,
                "city",
                "candidate_required_location",
                "LocationName",
                "locationName",
                "cityName",
                "workCity",
            ),
            "salary": self._first(item, "salary", "salaryDesc", "salaryRange"),
            "salary_min": self._first(item, "salary_min", "salaryMin"),
            "salary_max": self._first(item, "salary_max", "salaryMax"),
            "experience": self._first(
                item,
                "experience",
                "job_type",
                "RequireWorkYearsName",
                "requireWorkYearsName",
                "experienceName",
                "workYear",
                "jobExperience",
            ),
            "education": self._first(item, "education", "DegreeName", "degreeName", "educationName"),
            "description": description,
            "company_size": self._first(item, "companySize", "scaleName", "staffSize"),
            "industry": self._first(item, "industry", "industryName", "industryField"),
            "financing": self._first(item, "financing", "financeStage", "financeStageName"),
            "published_at": self._first(
                item,
                "published_at",
                "publication_date",
                "publishedAt",
                "LastUpdateTime",
                "lastUpdateTime",
                "publishTime",
                "updateTime",
                "createTime",
            ),
            "tags": item.get("tags") or item.get("jobLabels") or item.get("CategoryName") or [],
        }

    def _job_from_html(
        self,
        attrs: Mapping[str, str],
        body: str,
        *,
        source: Mapping[str, Any],
        base_url: str,
    ) -> Mapping[str, Any]:
        anchor = _ANCHOR_RE.search(body)
        anchor_attrs = _parse_attrs(anchor.group("attrs")) if anchor else {}
        title = _pick_attr(attrs, "data-title", "data-job", "data-position") or (
            _visible_text(anchor.group("body"), max_chars=200) if anchor else ""
        )
        raw_url = anchor_attrs.get("href", attrs.get("data-url", ""))
        source_url = self._absolute_source_url(raw_url or base_url, source=source, base_url=base_url)
        return {
            "provider_record_id": _pick_attr(attrs, "data-id", "data-job-id")
            or hashlib.sha256(source_url.encode()).hexdigest()[:24],
            "source_code": str(source.get("source_code") or source.get("code") or ""),
            "source_url": source_url,
            "apply_url": source_url,
            "title": title,
            "company": _pick_attr(attrs, "data-company") or _pick_by_class(body, "company"),
            "city": _pick_attr(attrs, "data-city") or _pick_by_class(body, "city"),
            "salary": _pick_attr(attrs, "data-salary") or _pick_by_class(body, "salary"),
            "experience": _pick_attr(attrs, "data-experience") or _pick_by_class(body, "experience"),
            "education": _pick_attr(attrs, "data-education") or _pick_by_class(body, "education"),
            "description": _pick_by_class(body, "description", "job-description", "detail", max_chars=50_000)
            or _visible_text(body),
            "published_at": _pick_attr(attrs, "data-published-at") or _pick_by_class(body, "published-at"),
        }

    @staticmethod
    def _first(mapping: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            value = mapping.get(key)
            if value is not None and str(value).strip():
                return sanitize_external_text(value, max_chars=2048)
        return ""

    def _absolute_source_url(
        self,
        raw_url: str,
        *,
        source: Mapping[str, Any],
        base_url: str,
    ) -> str:
        code = str(source.get("source_code") or source.get("code") or "").strip()
        registration = self._registry.require(code)
        candidate = urljoin(base_url, raw_url.strip() or base_url)
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() == "http":
            candidate = urlunsplit(
                ("https", parsed.netloc, parsed.path or "/", parsed.query, "")
            )
        return registration.validate_url(candidate)

    @staticmethod
    def _dedupe(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        unique: list[Mapping[str, Any]] = []
        for record in records:
            key = (
                str(record.get("source_code") or ""),
                str(record.get("provider_record_id") or ""),
                str(record.get("source_url") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(record)
        return unique


def build_jobs_provider_from_scraping_settings(
    scraping: ScrapingSettings,
    *,
    registry: SourceRegistry,
    mcp_server_config: Any | None = None,
    mcp_manager_factory: Any | None = None,
) -> ReadOnlyJobsProvider:
    """Build the configured read-only jobs provider.

    ``provider_server`` keeps the existing MCP path. Without it, OpenHarness
    uses its built-in public HTTPS provider over reviewed ``allowed_sources``.
    """

    if scraping.provider_server.strip():
        if mcp_server_config is None:
            raise UnsafeJobProviderError(f"MCP server 未配置: {scraping.provider_server}")
        if mcp_manager_factory is None:
            from openharness.mcp.client import McpClientManager

            mcp_manager_factory = lambda configs: McpClientManager(configs)
        manager = mcp_manager_factory({scraping.provider_server: mcp_server_config})
        return McpJobsProvider(
            manager,
            server_name=scraping.provider_server,
            provider_name=scraping.provider_name or scraping.provider_server,
            tool_map=scraping.provider_tools,
            max_response_bytes=scraping.max_response_bytes,
        )
    return SafePublicJobsProvider(
        scraping.allowed_sources,
        registry=registry,
        max_response_bytes=scraping.max_response_bytes,
        timeout_seconds=scraping.timeout_seconds,
        request_delay_min=scraping.request_delay_min,
        request_delay_max=scraping.request_delay_max,
        respect_robots_txt=scraping.respect_robots_txt,
        user_agent=(scraping.user_agents[0] if scraping.user_agents else "OpenHarnessJobFetcher/1.0"),
    )


@dataclass
class JobSyncReport:
    """Auditable result for one bounded provider synchronization."""

    collection_run_id: str
    provider: str
    source_code: str
    tool_name: str
    query_digest: str
    started_at: str
    finished_at: str = ""
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    stale_count: int = 0
    failed_count: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.collection_run_id,
            "provider": self.provider,
            "source_code": self.source_code,
            "tool_name": self.tool_name,
            "query_digest": self.query_digest,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "fetched_count": self.fetched_count,
            "inserted_count": self.inserted_count,
            "updated_count": self.updated_count,
            "skipped_count": self.skipped_count,
            "stale_count": self.stale_count,
            "failed_count": self.failed_count,
            "errors": list(self.errors or []),
            "server_version": "",
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)  # noqa: UP017, FURB162
    except ValueError:
        return None


class JobSearchService:
    """Search local snapshots first, then perform a bounded optional sync."""

    def __init__(
        self,
        store: Any,
        *,
        registry: SourceRegistry,
        provider: ReadOnlyJobsProvider | None = None,
        rag_store: Any | None = None,
        limits: JobSyncLimits | None = None,
        now: Any = _now,
    ) -> None:
        self.store = store
        self.registry = registry
        self.provider = provider
        self.rag_store = rag_store
        self.limits = limits or JobSyncLimits()
        self._now = now

    def search(
        self,
        query: JobSearchQuery,
        *,
        force_sync: bool = False,
    ) -> tuple[list[dict[str, Any]], JobSyncReport | None, list[str]]:
        """Return local-first results and optionally synchronize a provider."""

        cached = self._local_matches(query)
        rag_results = self._rag_matches(query, exclude_ids={str(item.get("id")) for item in cached})
        combined = [*cached, *rag_results][: query.limit]
        fresh = any(self._is_fresh(item) for item in combined)
        if combined and fresh and not force_sync:
            return combined, None, ["结果来自本地岗位快照/RAG，未访问外部服务。"]
        if self.provider is None:
            return combined, None, ["没有配置受信任只读岗位服务，保留本地结果。"]
        report = self.sync(query)
        refreshed = self._local_matches(query)
        if not refreshed:
            refreshed = combined
        notes = ["岗位搜索优先使用本地快照和 RAG。"]
        if report.failed_count:
            notes.append("外部岗位服务不可用，已降级到本地历史快照并标记为 stale。")
        return refreshed[: query.limit], report, notes

    def sync(self, query: JobSearchQuery) -> JobSyncReport:
        """Run one serialized, allowlisted, auditable provider synchronization."""

        started = _iso(self._now())
        report = JobSyncReport(
            collection_run_id=f"job-sync-{uuid.uuid4().hex[:16]}",
            provider=getattr(self.provider, "provider_name", "unknown"),
            source_code="",
            tool_name="search_jobs",
            query_digest=hashlib.sha256(
                json.dumps(query.to_provider_args(max_results=self.limits.max_results), sort_keys=True).encode()
            ).hexdigest()[:24],
            started_at=started,
            errors=[],
        )
        try:
            if self.provider is None:
                raise JobProviderError("no provider configured")
            source_payload = list(self.provider.list_sources())
            self.registry.validate_provider_sources(source_payload)
            if source_payload:
                report.source_code = str(
                    source_payload[0].get("source_code") or source_payload[0].get("code") or ""
                )
            raw_jobs = list(
                self.provider.search_jobs(
                    query.to_provider_args(max_results=self.limits.max_results)
                )
            )
            if len(raw_jobs) > self.limits.max_results:
                report.skipped_count += len(raw_jobs) - self.limits.max_results
                raw_jobs = raw_jobs[: self.limits.max_results]
            detail_count = 0
            normalized: list[JobRecord] = []
            for raw in raw_jobs:
                report.fetched_count += 1
                candidate = dict(raw)
                source_url = str(
                    candidate.get("source_url")
                    or candidate.get("sourceUrl")
                    or candidate.get("url")
                    or ""
                )
                record_id = str(
                    candidate.get("provider_record_id")
                    or candidate.get("providerRecordId")
                    or candidate.get("job_id")
                    or candidate.get("jobId")
                    or candidate.get("id")
                    or ""
                )
                if not candidate.get("description") and detail_count < self.limits.max_details:
                    if not source_url or not record_id:
                        raise JobSchemaError("detail lookup requires provider id and source URL")
                    # The source URL is only passed after source validation below.
                    source_code = str(candidate.get("source_code") or candidate.get("sourceCode") or "")
                    registration = self.registry.require(source_code)
                    approved_url = registration.validate_url(source_url)
                    detail = self.provider.get_job_details(record_id, source_url=approved_url)
                    candidate.update(dict(detail))
                    detail_count += 1
                normalized.append(
                    normalize_job_record(
                        candidate,
                        provider=getattr(self.provider, "provider_name", "unknown"),
                        registry=self.registry,
                        fetched_at=started,
                    )
                )
            accepted = self._dedupe_records(normalized, report)
            self._persist_records(accepted, report)
        except Exception as exc:  # noqa: BLE001
            report.failed_count += 1
            report.errors.append(self._safe_error(exc))
            report.stale_count = self._mark_stale(report.provider)
        report.finished_at = _iso(self._now())
        self.store.append_sync_run(report.to_dict())
        return report

    def _local_matches(self, query: JobSearchQuery) -> list[dict[str, Any]]:
        jobs = self.store.load_jobs()
        scored: list[tuple[int, dict[str, Any]]] = []
        terms = [term.lower() for term in re.findall(r"\w+|[\u4e00-\u9fff]", query.query.lower()) if term]
        for job in jobs:
            if query.city and str(job.get("city", "")) != query.city:
                continue
            if query.experience and query.experience not in str(job.get("experience", "")):
                continue
            if query.education and query.education not in str(job.get("education", "")):
                continue
            salary_max = self._safe_int(job.get("salary_max"))
            salary_min = self._safe_int(job.get("salary_min"))
            if query.salary_min is not None and salary_max < query.salary_min:
                continue
            if query.salary_max is not None and salary_min > query.salary_max:
                continue
            haystack = " ".join(
                str(job.get(key, ""))
                for key in ("title", "company", "city", "salary", "experience", "education", "description")
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if terms and score == 0:
                continue
            scored.append((score, job))
        scored.sort(key=lambda item: (item[0], str(item[1].get("last_seen_at", ""))), reverse=True)
        return [item[1] for item in scored[: query.limit]]

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _rag_matches(self, query: JobSearchQuery, *, exclude_ids: set[str]) -> list[dict[str, Any]]:
        if self.rag_store is None:
            return []
        try:
            hits = self.rag_store.search("jobs", query.query or "工程师", top_k=query.limit)
        except Exception:  # noqa: BLE001
            return []
        results: list[dict[str, Any]] = []
        for hit in hits:
            metadata = dict(getattr(hit, "metadata", {}) or {})
            job_id = str(metadata.get("job_id") or getattr(hit, "id", ""))
            if not job_id or job_id in exclude_ids:
                continue
            results.append(
                {
                    "id": job_id,
                    "title": str(metadata.get("title") or ""),
                    "company": str(metadata.get("company") or ""),
                    "city": str(metadata.get("city") or ""),
                    "salary": str(metadata.get("salary") or ""),
                    "description": str(getattr(hit, "text", "") or ""),
                    "source_code": str(metadata.get("source_code") or ""),
                    "source_site": str(metadata.get("source_site") or ""),
                    "source_url": str(metadata.get("source_url") or ""),
                    "apply_url": str(metadata.get("apply_url") or ""),
                    "provider": str(metadata.get("provider") or ""),
                    "provider_record_id": str(metadata.get("provider_record_id") or ""),
                    "fetched_at": str(metadata.get("fetched_at") or ""),
                    "last_seen_at": str(metadata.get("last_seen_at") or ""),
                    "provenance_status": str(metadata.get("provenance_status") or "stale"),
                    "status": str(metadata.get("status") or "stale"),
                }
            )
        return results[: query.limit]

    def _is_fresh(self, job: Mapping[str, Any]) -> bool:
        fetched = _parse_time(job.get("fetched_at"))
        return bool(fetched and fetched >= self._now() - timedelta(hours=self.limits.freshness_hours))

    @staticmethod
    def _dedupe_records(records: Sequence[JobRecord], report: JobSyncReport) -> list[JobRecord]:
        seen_provider_ids: set[tuple[str, str]] = set()
        seen_urls: set[tuple[str, str]] = set()
        seen_composites: set[tuple[str, str, str, str, str]] = set()
        unique: list[JobRecord] = []
        for record in records:
            provider_key = (record.provider, record.provider_record_id)
            url_key = (record.source_code, record.source_url)
            composite_key = (
                record.source_code,
                record.company,
                record.title,
                record.city,
                record.content_hash,
            )
            if (
                provider_key in seen_provider_ids
                or url_key in seen_urls
                or composite_key in seen_composites
            ):
                report.skipped_count += 1
                continue
            seen_provider_ids.add(provider_key)
            seen_urls.add(url_key)
            seen_composites.add(composite_key)
            unique.append(record)
        return unique

    def _persist_records(self, records: Sequence[JobRecord], report: JobSyncReport) -> None:
        old_jobs = self.store.load_jobs()
        merged = list(old_jobs)

        def rebuild_indexes() -> tuple[
            dict[tuple[str, str], int],
            dict[tuple[str, str], int],
            dict[tuple[str, str, str, str, str], int],
        ]:
            by_provider_id: dict[tuple[str, str], int] = {}
            by_url: dict[tuple[str, str], int] = {}
            by_composite: dict[tuple[str, str, str, str, str], int] = {}
            for index, item in enumerate(merged):
                by_provider_id[
                    (str(item.get("provider", "")), str(item.get("provider_record_id", "")))
                ] = index
                by_url[(str(item.get("source_code", "")), str(item.get("source_url", "")))] = index
                by_composite[
                    (
                        str(item.get("source_code", "")),
                        str(item.get("company", "")),
                        str(item.get("title", "")),
                        str(item.get("city", "")),
                        str(item.get("content_hash", "")),
                    )
                ] = index
            return by_provider_id, by_url, by_composite

        for record in records:
            by_provider_id, by_url, by_composite = rebuild_indexes()
            existing_index = by_provider_id.get((record.provider, record.provider_record_id))
            if existing_index is None:
                existing_index = by_url.get((record.source_code, record.source_url))
            if existing_index is None:
                existing_index = by_composite.get(
                    (record.source_code, record.company, record.title, record.city, record.content_hash)
                )
            existing = merged[existing_index] if existing_index is not None else None
            payload = record.to_dict()
            if existing is None:
                merged.append(payload)
                report.inserted_count += 1
            else:
                persisted_id = str(existing.get("id") or record.id)
                payload["id"] = persisted_id
                assert existing_index is not None
                merged[existing_index] = {
                    **existing,
                    **payload,
                    "provenance_status": "verified",
                    "status": "active",
                }
                if (
                    str(existing.get("content_hash", "")) == record.content_hash
                    and persisted_id == record.id
                ):
                    report.skipped_count += 1
                else:
                    report.updated_count += 1
            self._project_to_rag(record, existing, persisted_id=str(payload["id"]))
        self.store.save_jobs(merged)

    def _project_to_rag(
        self,
        record: JobRecord,
        existing: Mapping[str, Any] | None,
        *,
        persisted_id: str,
    ) -> None:
        if self.rag_store is None:
            return
        if (
            existing is not None
            and str(existing.get("content_hash", "")) == record.content_hash
            and str(existing.get("id", "")) == persisted_id
        ):
            return
        try:
            self.rag_store.delete_documents("jobs", where={"job_id": persisted_id})
            metadata = record.to_rag_metadata()
            metadata["job_id"] = persisted_id
            self.rag_store.add_documents(
                "jobs",
                [{"id": persisted_id, "text": record.to_rag_text(), "metadata": metadata}],
            )
        except Exception:  # noqa: BLE001
            # JSON cache remains authoritative when optional RAG projection fails.
            return

    def _mark_stale(self, provider: str) -> int:
        jobs = self.store.load_jobs()
        count = 0
        for job in jobs:
            if str(job.get("provider", "")) == provider and job.get("provenance_status") != "invalid":
                job["provenance_status"] = "stale"
                job["status"] = "stale"
                count += 1
        if count:
            self.store.save_jobs(jobs)
        return count

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, (SourceValidationError, JobSchemaError, JobDataError, JobProviderError)):
            return _SENSITIVE_ERROR_RE.sub(r"\1=<redacted>", str(exc))[:300]
        return f"{exc.__class__.__name__}: provider request failed"
