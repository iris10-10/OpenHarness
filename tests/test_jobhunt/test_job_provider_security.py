from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openharness.config.schema import ScrapingSettings
from openharness.jobhunt.api.app import create_app
from openharness.jobhunt.job_provider import (
    JobProviderError,
    JobSearchQuery,
    JobSearchService,
    JobSyncLimits,
    McpJobsProvider,
    SafePublicJobsProvider,
    UnsafeJobProviderError,
)
from openharness.jobhunt.job_schema import (
    JobDataError,
    JobSchemaError,
    SourceRegistry,
    SourceValidationError,
    canonicalize_https_url,
    normalize_job_record,
    parse_json_payload,
    sanitize_external_text,
)
from openharness.jobhunt.storage import JobHuntStore
from openharness.mcp.types import McpConnectionStatus, McpHttpServerConfig, McpToolInfo
from openharness.rag.sources import BossScraper


def _registry() -> SourceRegistry:
    return SourceRegistry.from_mappings(
        [
            {
                "source_code": "provider_x",
                "source_site": "示例岗位服务",
                "allowed_domains": ["jobs.example.com"],
            }
        ]
    )


def _job(record_id: str = "abc123", **updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": record_id,
        "provider_record_id": record_id,
        "source_code": "provider_x",
        "source_url": f"https://jobs.example.com/jobs/{record_id}",
        "apply_url": f"https://jobs.example.com/jobs/{record_id}",
        "title": "Python 后端工程师",
        "company": "示例科技",
        "city": "杭州",
        "salary": "25-40K",
        "description": "<p>负责 API</p><script>alert(1)</script>",
    }
    payload.update(updates)
    return payload


def _workspace_tmp(name: str) -> Path:
    path = Path.cwd() / ".verify-web" / "job-provider-security" / f"{name}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class _Provider:
    provider_name = "trusted_jobs_mcp"

    def __init__(self, jobs: list[dict[str, Any]], *, fail: bool = False) -> None:
        self.jobs = jobs
        self.fail = fail

    def list_sources(self) -> list[dict[str, Any]]:
        if self.fail:
            raise JobProviderError("temporary failure with token=secret")
        return [{"source_code": "provider_x", "domains": ["jobs.example.com"]}]

    def search_jobs(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        assert "resume_text" not in query
        return self.jobs

    def get_job_details(self, provider_record_id: str, *, source_url: str) -> dict[str, Any]:
        return {
            "provider_record_id": provider_record_id,
            "source_url": source_url,
            "description": "详情补充",
        }


class _Manager:
    def __init__(
        self,
        *,
        config: McpHttpServerConfig | None = None,
        tool_names: list[str] | None = None,
    ) -> None:
        self.config = config or McpHttpServerConfig(type="http", url="https://mcp.example.com/mcp")
        self.tool_names = tool_names or ["list_sources", "search_jobs", "get_job_details"]

    def get_server_config(self, name: str) -> McpHttpServerConfig:
        assert name == "jobs"
        return self.config

    async def connect_all(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def list_statuses(self) -> list[McpConnectionStatus]:
        return [
            McpConnectionStatus(
                name="jobs",
                state="connected",
                transport="http",
                tools=[
                    McpToolInfo(
                        server_name="jobs",
                        name=name,
                        description="",
                        input_schema={"type": "object"},
                    )
                    for name in self.tool_names
                ],
            )
        ]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        del server_name, arguments
        if tool_name == "list_sources":
            return json.dumps({"sources": [{"source_code": "provider_x", "domains": ["jobs.example.com"]}]})
        if tool_name == "search_jobs":
            return json.dumps({"jobs": [_job()]})
        return json.dumps({"job": _job()})


def test_url_validation_and_external_text_sanitization() -> None:
    assert canonicalize_https_url(
        "https://jobs.example.com/jobs/1?x=1#fragment",
        allowed_domains=["jobs.example.com"],
    ) == "https://jobs.example.com/jobs/1?x=1"
    for url in (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://localhost/jobs/1",
        "https://169.254.169.254/latest/meta-data",
        "https://evil.example.net/jobs/1",
        "https://jobs.example.com/jobs/1?token=secret",
    ):
        with pytest.raises(SourceValidationError):
            canonicalize_https_url(url, allowed_domains=["jobs.example.com"])
    cleaned = sanitize_external_text(
        "<form><input name=password></form><p>岗位</p><script>tool()</script>\x00",
        max_chars=10,
    )
    assert cleaned == "岗位"


def test_normalize_job_record_requires_registered_https_source() -> None:
    record = normalize_job_record(_job(), provider="trusted_jobs_mcp", registry=_registry())
    assert record.id == "job:trusted_jobs_mcp:abc123"
    assert record.source_site == "示例岗位服务"
    assert "alert" not in record.description

    with pytest.raises(JobSchemaError):
        normalize_job_record(
            _job(title=""),
            provider="trusted_jobs_mcp",
            registry=_registry(),
        )
    with pytest.raises(SourceValidationError):
        normalize_job_record(
            _job(source_url="https://evil.example.net/jobs/abc123"),
            provider="trusted_jobs_mcp",
            registry=_registry(),
        )


def test_sensitive_query_and_response_size_are_rejected() -> None:
    with pytest.raises(ValueError):
        JobSearchQuery(query="Python cookie: session=abc")
    with pytest.raises(ValueError):
        JobSearchQuery(query="联系我 13800138000")
    with pytest.raises(JobDataError):
        parse_json_payload('{"items": []}', max_bytes=4)


def test_mcp_provider_requires_https_and_declared_tool_allowlist() -> None:
    insecure = _Manager(config=McpHttpServerConfig(type="http", url="http://mcp.example.com/mcp"))
    with pytest.raises(UnsafeJobProviderError):
        McpJobsProvider(insecure, server_name="jobs")

    with pytest.raises(UnsafeJobProviderError):
        McpJobsProvider(_Manager(), server_name="jobs", tool_map={"fetch_url": "fetch_url"})

    blocked = McpJobsProvider(_Manager(tool_names=["list_sources", "search_jobs", "get_job_details", "apply_job"]), server_name="jobs")
    with pytest.raises(UnsafeJobProviderError):
        blocked.list_sources()

    provider = McpJobsProvider(_Manager(), server_name="jobs")
    assert provider.list_sources()[0]["source_code"] == "provider_x"


def test_safe_public_jobs_provider_fetches_reviewed_sources_only() -> None:
    registry = _registry()
    source = {
        "source_code": "provider_x",
        "source_site": "示例岗位服务",
        "allowed_domains": ["jobs.example.com"],
        "search_url_template": "https://jobs.example.com/search?q={query}&city={city}&limit={limit}",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("cookie") is None
        assert request.headers.get("authorization") is None
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "safe-1",
                            "source_code": "provider_x",
                            "source_url": "https://jobs.example.com/jobs/safe-1",
                            "title": "Python 后端工程师",
                            "company": "示例科技",
                            "city": "杭州",
                            "salary": "25-40K",
                            "description": "<p>负责 API</p><script>alert(1)</script>",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    provider = SafePublicJobsProvider(
        [source],
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="https://jobs.example.com"),
        request_delay_min=0,
        request_delay_max=0,
        respect_robots_txt=False,
    )

    jobs = list(provider.search_jobs({"query": "Python", "city": "杭州", "limit": 5, "page": 1}))

    assert jobs[0]["provider_record_id"] == "safe-1"
    assert jobs[0]["source_url"] == "https://jobs.example.com/jobs/safe-1"
    assert "script" not in normalize_job_record(jobs[0], provider=provider.provider_name, registry=registry).description


def test_safe_public_jobs_provider_upgrades_approved_http_job_links() -> None:
    registry = _registry()
    source = {
        "source_code": "provider_x",
        "source_site": "示例岗位服务",
        "allowed_domains": ["jobs.example.com"],
        "search_url_template": "https://jobs.example.com/search?q={query}",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "safe-http-1",
                        "source_code": "provider_x",
                        "source_url": "http://jobs.example.com/jobs/safe-http-1",
                        "title": "Python 后端工程师",
                        "company": "示例科技",
                    }
                ]
            },
        )

    provider = SafePublicJobsProvider(
        [source],
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_min=0,
        request_delay_max=0,
        respect_robots_txt=False,
    )

    jobs = list(provider.search_jobs({"query": "Python", "limit": 5, "page": 1}))

    assert jobs[0]["source_url"] == "https://jobs.example.com/jobs/safe-http-1"


def test_safe_public_jobs_provider_rejects_unapproved_source_redirects() -> None:
    registry = _registry()
    source = {
        "source_code": "provider_x",
        "source_site": "示例岗位服务",
        "allowed_domains": ["jobs.example.com"],
        "search_url_template": "https://jobs.example.com/search?q={query}",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.example.net":
            return httpx.Response(200, text="bad")
        return httpx.Response(302, headers={"Location": "https://evil.example.net/jobs/1"})

    provider = SafePublicJobsProvider(
        [source],
        registry=registry,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_min=0,
        request_delay_max=0,
        respect_robots_txt=False,
    )

    with pytest.raises(SourceValidationError):
        provider.search_jobs({"query": "Python", "limit": 5, "page": 1})


def test_sync_persists_jobs_deduplicates_and_marks_stale_on_failure() -> None:
    tmp_path = _workspace_tmp("sync")
    store = JobHuntStore(tmp_path)
    service = JobSearchService(
        store,
        registry=_registry(),
        provider=_Provider([_job("abc123"), _job("dup", source_url="https://jobs.example.com/jobs/abc123")]),
        limits=JobSyncLimits(max_results=10, max_details=10),
    )

    report = service.sync(JobSearchQuery(query="Python", limit=10))

    assert report.inserted_count == 1
    assert report.skipped_count == 1
    jobs = store.load_jobs()
    assert len(jobs) == 1
    assert jobs[0]["source_url"] == "https://jobs.example.com/jobs/abc123"
    assert store.load_sync_runs()[0]["query_digest"]
    assert "Python" not in json.dumps(store.load_sync_runs(), ensure_ascii=False)

    failed = JobSearchService(
        store,
        registry=_registry(),
        provider=_Provider([], fail=True),
    )
    failed_report = failed.sync(JobSearchQuery(query="Python", limit=10))

    assert failed_report.failed_count == 1
    assert failed_report.stale_count == 1
    assert store.load_jobs()[0]["provenance_status"] == "stale"
    assert "secret" not in json.dumps(store.load_sync_runs(), ensure_ascii=False)


def test_account_safe_mode_rejects_recruitment_credentials() -> None:
    with pytest.raises(ValidationError):
        ScrapingSettings(boss={"cookie": "session=abc"})


def test_legacy_job_scraper_cannot_fetch_without_fixture_client() -> None:
    tmp_path = _workspace_tmp("scraper")
    scraper = BossScraper(state_path=tmp_path / "boss.json")
    with pytest.raises(PermissionError):
        scraper.fetch("https://www.zhipin.com/web/geek/job?query=Python")


def test_jobs_web_api_is_local_first_and_rejects_untrusted_manual_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = _workspace_tmp("web")
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_JOBHUNT_DIR", str(tmp_path / "jobhunt"))
    client = TestClient(create_app(static_dir=tmp_path / "missing-dist"))

    status = client.get("/api/jobs/sync/status")
    assert status.status_code == 200
    assert status.json()["account_safe_mode"] is True
    assert status.json()["provider_configured"] is False

    listing = client.get("/api/jobs", params={"query": "Python"})
    assert listing.status_code == 200
    payload = listing.json()
    assert payload["total"] >= 1
    assert payload["items"][0]["source_site"]
    assert "fetched_at" in payload["items"][0]

    sync = client.post("/api/jobs/sync", json={"query": "Python", "limit": 5})
    assert sync.status_code == 409

    imported = client.post(
        "/api/jobs/import",
        json={
            "title": "测试岗位",
            "company": "测试公司",
            "source_url": "javascript:alert(1)",
        },
    )
    assert imported.status_code == 422
