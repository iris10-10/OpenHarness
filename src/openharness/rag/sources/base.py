"""Common scraper primitives for RAG data sources.

The concrete sources in this package keep network behavior conservative:
request pacing is configurable, user agents rotate per request, retries use
exponential backoff, and URL hashes are persisted so repeated runs are
deduplicated and can resume incrementally.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from openharness.config.paths import get_data_dir
from openharness.rag.utils import content_hash
from openharness.utils.fs import atomic_write_text

DEFAULT_USER_AGENTS: tuple[str, ...] = (
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
)

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", re.DOTALL)


@dataclass(frozen=True)
class ScrapedDocument:
    """Normalized item ready for ingestion into a vector collection."""

    id: str
    text: str
    metadata: dict[str, Any]


@dataclass
class ScrapeReport:
    """Summary for one scraper run."""

    fetched: int = 0
    skipped_duplicates: int = 0
    failed: int = 0
    documents: list[ScrapedDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScraperConfig:
    """Runtime controls shared by all scrapers."""

    request_interval_min: float = 2.0
    request_interval_max: float = 5.0
    max_retries: int = 3
    backoff_base_seconds: float = 0.5
    timeout_seconds: float = 30.0
    user_agents: Sequence[str] = DEFAULT_USER_AGENTS
    proxies: Mapping[str, str] | None = None
    cookies: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = None
    respect_robots_txt: bool = True


def normalize_space(text: str) -> str:
    """Collapse whitespace in parsed page text."""

    return _SPACE_RE.sub(" ", text).strip()


def strip_html(markup: str) -> str:
    """Dependency-free HTML-to-text helper used by scraper parsers."""

    return normalize_space(_TAG_RE.sub(" ", markup))


def parse_attrs(tag_text: str) -> dict[str, str]:
    """Return lower-cased HTML attributes from a start tag or tag-like string."""

    return {match.group(1).lower(): match.group(3) for match in _ATTR_RE.finditer(tag_text)}


def find_json_objects(value: Any, required_keys: Iterable[str]) -> list[dict[str, Any]]:
    """Recursively return dicts containing any of the required keys."""

    keys = {key.lower() for key in required_keys}
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        lowered = {str(key).lower() for key in value}
        if lowered.intersection(keys):
            found.append(value)
        for child in value.values():
            found.extend(find_json_objects(child, keys))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_json_objects(child, keys))
    return found


def extract_json_payloads(text: str) -> list[Any]:
    """Extract JSON objects from raw JSON or script tags."""

    payloads: list[Any] = []
    try:
        payloads.append(json.loads(text))
        return payloads
    except ValueError:
        pass
    script_re = re.compile(r"<script\b[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    for match in script_re.finditer(text):
        body = match.group(1).strip()
        if not body:
            continue
        for candidate in (body, _extract_assignment_json(body)):
            if not candidate:
                continue
            try:
                payloads.append(json.loads(candidate))
                break
            except ValueError:
                continue
    return payloads


def _extract_assignment_json(script_body: str) -> str:
    """Return the first object/array literal from a simple JS assignment."""

    start_positions = [pos for pos in (script_body.find("{"), script_body.find("[")) if pos >= 0]
    if not start_positions:
        return ""
    start = min(start_positions)
    return script_body[start:].rstrip(";")


class BaseScraper(ABC):
    """Base class with rate limiting, retries, UA rotation and URL-state dedupe."""

    source_name = "base"

    def __init__(
        self,
        *,
        config: ScraperConfig | None = None,
        state_path: str | Path | None = None,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config or ScraperConfig()
        self._state_path = Path(state_path) if state_path is not None else None
        self._client = client
        self._owns_client = client is None
        self._sleeper = sleeper
        self._now = now
        self._rng = rng or random.Random()
        self._last_request_at: float | None = None
        self._state_loaded = False
        self._state: dict[str, Any] = {}
        self._robots: dict[str, RobotFileParser] = {}

    def close(self) -> None:
        """Close the internally owned HTTP client."""

        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @abstractmethod
    def collect(self, *args: Any, **kwargs: Any) -> ScrapeReport:
        """Collect source records and return a report."""

    # ------------------------------------------------------------------ HTTP

    def fetch(self, url: str, *, params: Mapping[str, Any] | None = None) -> str:
        """Fetch a page with pacing, UA rotation and retry/backoff."""

        if self.config.respect_robots_txt and not self._robots_allows(url):
            raise PermissionError(f"robots.txt disallows scraping {url}")
        last_error: Exception | None = None
        attempts = max(1, self.config.max_retries)
        for attempt in range(attempts):
            self._rate_limit()
            try:
                response = self._http_client().get(
                    url,
                    params=dict(params or {}),
                    headers=self._request_headers(),
                    cookies=dict(self.config.cookies or {}),
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.text
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                self._sleeper(self.config.backoff_base_seconds * (2**attempt))
        raise RuntimeError(f"Failed to fetch {url}") from last_error

    def _http_client(self) -> httpx.Client:
        if self._client is None:
            proxy = self._proxy_url()
            if proxy:
                self._client = httpx.Client(timeout=self.config.timeout_seconds, proxy=proxy)
            else:
                self._client = httpx.Client(timeout=self.config.timeout_seconds)
        return self._client

    def _proxy_url(self) -> str:
        proxies = dict(self.config.proxies or {})
        if not proxies:
            return ""
        for key in ("https", "https://", "http", "http://"):
            value = proxies.get(key)
            if value:
                return str(value)
        first = next(iter(proxies.values()), "")
        return str(first or "")

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.config.headers or {})
        agents = list(self.config.user_agents or DEFAULT_USER_AGENTS)
        headers["User-Agent"] = self._rng.choice(agents)
        headers.setdefault("Accept", "text/html,application/json;q=0.9,*/*;q=0.8")
        return headers

    def _rate_limit(self) -> None:
        now = self._now()
        if self._last_request_at is not None:
            minimum = max(0.0, self.config.request_interval_min)
            maximum = max(minimum, self.config.request_interval_max)
            delay = self._rng.uniform(minimum, maximum)
            elapsed = max(0.0, now - self._last_request_at)
            if delay > elapsed:
                self._sleeper(delay - elapsed)
                now = self._now()
        self._last_request_at = now

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return True
        root = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._robots.get(root)
        if parser is None:
            parser = RobotFileParser()
            parser.set_url(urljoin(root, "/robots.txt"))
            try:
                parser.read()
            except (OSError, ValueError):
                return True
            self._robots[root] = parser
        agent = (self.config.user_agents or DEFAULT_USER_AGENTS)[0]
        return parser.can_fetch(agent, url)

    # ------------------------------------------------------------------ state

    @property
    def state_path(self) -> Path:
        """Return the JSON state path used for dedupe/incremental updates."""

        if self._state_path is not None:
            return self._state_path
        return get_data_dir() / "rag" / "sources" / f"{self.source_name}.json"

    def url_key(self, url: str) -> str:
        """Stable hash key for a URL."""

        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

    def has_seen_url(self, url: str) -> bool:
        """Return whether this URL was already collected."""

        return self.url_key(url) in self._load_state().get("seen_urls", {})

    def mark_seen_url(self, url: str, *, updated_at: str | None = None) -> None:
        """Persist a URL as collected."""

        state = self._load_state()
        seen = state.setdefault("seen_urls", {})
        seen[self.url_key(url)] = {"url": url, "updated_at": updated_at or self._now()}
        self._save_state()

    def high_watermark(self, namespace: str = "default") -> str | None:
        """Return the last incremental watermark for a namespace."""

        value = self._load_state().get("watermarks", {}).get(namespace)
        return str(value) if value is not None else None

    def set_high_watermark(self, value: str, namespace: str = "default") -> None:
        """Persist the last incremental watermark for a namespace."""

        state = self._load_state()
        state.setdefault("watermarks", {})[namespace] = value
        self._save_state()

    def should_collect(self, url: str, *, updated_at: str | None = None) -> bool:
        """Return whether a URL/date pair is new for incremental collection."""

        if self.has_seen_url(url):
            return False
        watermark = self.high_watermark()
        return not (updated_at and watermark and updated_at <= watermark)

    def document_id(self, prefix: str, url: str, *parts: str) -> str:
        """Build a deterministic document id for a scraped item."""

        seed = "::".join(part for part in (url, *parts) if part)
        return f"{prefix}:{content_hash(seed, length=20)}"

    def _load_state(self) -> dict[str, Any]:
        if self._state_loaded:
            return self._state
        self._state_loaded = True
        path = self.state_path
        if not path.exists():
            self._state = {"version": 1, "seen_urls": {}, "watermarks": {}}
            return self._state
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self._state = {
            "version": 1,
            "seen_urls": dict(raw.get("seen_urls") or {}) if isinstance(raw, dict) else {},
            "watermarks": dict(raw.get("watermarks") or {}) if isinstance(raw, dict) else {},
        }
        return self._state

    def _save_state(self) -> None:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(self._state, ensure_ascii=False, indent=2))
