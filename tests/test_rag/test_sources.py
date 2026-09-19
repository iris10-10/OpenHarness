"""Tests for Phase 4 RAG data sources and local knowledge import."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import httpx

from openharness.config.settings import Settings, _apply_env_overrides
from openharness.rag.sources import (
    BossScraper,
    GithubScraper,
    LeetcodeScraper,
    LocalKnowledgeImporter,
    NowcoderScraper,
    ScraperConfig,
)
from openharness.rag.vectorstore import VectorStore


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append({"url": url, **kwargs})
        request = httpx.Request("GET", url, params=kwargs.get("params"))
        text = self.responses.pop(0) if self.responses else "{}"
        return httpx.Response(200, text=text, request=request)

    def close(self) -> None:
        return None


def _fast_config(**updates: Any) -> ScraperConfig:
    values = {
        "request_interval_min": 0.0,
        "request_interval_max": 0.0,
        "respect_robots_txt": False,
        "max_retries": 1,
        "user_agents": ("UA-A", "UA-B"),
    }
    values.update(updates)
    return ScraperConfig(**values)


def test_boss_scraper_parses_mock_json_fields(tmp_path: Path) -> None:
    payload = {
        "zpData": {
            "jobList": [
                {
                    "jobName": "Python 后端工程师",
                    "brandName": "星河科技",
                    "cityName": "北京",
                    "salaryDesc": "25-40K",
                    "experienceName": "3-5年",
                    "degreeName": "本科",
                    "jobDescription": "负责 API 与 MySQL 性能优化",
                    "scaleName": "100-499人",
                    "industryName": "互联网",
                    "financeStageName": "B轮",
                    "publishTime": "2026-09-18",
                    "jobUrl": "/job/1.html",
                }
            ]
        }
    }
    scraper = BossScraper(config=_fast_config(), state_path=tmp_path / "boss.json")

    jobs = scraper.parse_search_results(json.dumps(payload, ensure_ascii=False), base_url="https://x.test")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Python 后端工程师"
    assert job.company == "星河科技"
    assert job.salary == "25-40K"
    assert job.url == "https://x.test/job/1.html"
    document = job.to_document()
    assert document.metadata["doc_type"] == "job_posting"
    assert "MySQL" in document.text


def test_scraper_collect_deduplicates_and_tracks_incremental_state(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "jobs": [
                {
                    "title": "Java 后端",
                    "company": "未来引擎",
                    "city": "上海",
                    "salary": "20-35K",
                    "description": "负责高并发服务",
                    "publishedAt": "2026-09-18",
                    "url": "https://jobs.test/java",
                }
            ]
        },
        ensure_ascii=False,
    )
    state = tmp_path / "boss-state.json"
    first_client = _FakeClient([body])
    first = BossScraper(config=_fast_config(), state_path=state, client=first_client)

    first_report = first.collect("Java", pages=1)

    assert first_report.fetched == 1
    assert len(first_report.documents) == 1
    assert first.high_watermark() == "2026-09-18"

    second_client = _FakeClient([body])
    second = BossScraper(config=_fast_config(), state_path=state, client=second_client)
    second_report = second.collect("Java", pages=1)

    assert second_report.documents == []
    assert second_report.skipped_duplicates == 1


def test_base_scraper_applies_rate_limit_and_user_agent_headers(tmp_path: Path) -> None:
    now = {"value": 100.0}
    sleeps: list[float] = []

    def fake_now() -> float:
        return now["value"]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["value"] += seconds

    client = _FakeClient(["{}", "{}"])
    scraper = BossScraper(
        config=_fast_config(request_interval_min=2.0, request_interval_max=2.0),
        state_path=tmp_path / "ua.json",
        client=client,
        sleeper=fake_sleep,
        now=fake_now,
        rng=random.Random(1),
    )

    scraper.fetch("https://example.test/a")
    scraper.fetch("https://example.test/b")

    assert sleeps == [2.0]
    headers = [request["headers"]["User-Agent"] for request in client.requests]
    assert set(headers).issubset({"UA-A", "UA-B"})


def test_interview_scrapers_parse_mock_records(tmp_path: Path) -> None:
    html = """
    <article class="interview-card" data-title="字节后端一面" data-company="字节跳动"
      data-position="后端工程师" data-round="一面" data-date="2026-09-01">
      <a href="/discuss/1">字节后端一面</a>
      <div class="content">问: MySQL 索引为什么失效？; Redis 如何做限流？</div>
      <div class="result">通过</div>
    </article>
    """
    nowcoder = NowcoderScraper(config=_fast_config(), state_path=tmp_path / "nowcoder.json")
    leetcode = LeetcodeScraper(config=_fast_config(), state_path=tmp_path / "leetcode.json")

    first = nowcoder.parse_experiences(html, base_url="https://www.nowcoder.com")[0]
    second = leetcode.parse_experiences(html, base_url="https://leetcode.cn")[0]

    assert first.company == "字节跳动"
    assert first.position == "后端工程师"
    assert "MySQL" in first.questions[0]
    assert second.to_document().metadata["doc_type"] == "interview_experience"


def test_github_scraper_parses_company_open_source_signals(tmp_path: Path) -> None:
    payload = {
        "items": [
            {
                "full_name": "openharness/example",
                "name": "example",
                "description": "Agent automation toolkit",
                "html_url": "https://github.com/openharness/example",
                "homepage": "https://example.test",
                "updated_at": "2026-09-17T00:00:00Z",
                "owner": {"login": "openharness"},
            }
        ]
    }
    scraper = GithubScraper(config=_fast_config(), state_path=tmp_path / "github.json")

    companies = scraper.parse_companies(json.dumps(payload), base_url="https://api.github.com/search")

    assert len(companies) == 1
    company = companies[0]
    assert company.name == "openharness/example"
    assert company.repositories == ["openharness/example"]
    assert company.website == "https://example.test"


def test_local_importer_imports_tags_and_skips_unchanged(
    tmp_path: Path,
    store: VectorStore,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "interview.md").write_text("# 面经\n\n线程池参数如何设置？", encoding="utf-8")
    importer = LocalKnowledgeImporter(store, state_path=tmp_path / "local-state.json")

    first = importer.import_directory(
        docs,
        collection="knowledge",
        tags=["interview", "java"],
        category="面试",
    )
    second = importer.import_directory(
        docs,
        collection="knowledge",
        tags=["interview", "java"],
        category="面试",
    )

    assert first.files_processed == 1
    assert first.chunks_created >= 1
    assert second.files_skipped == 1
    stored = store.get_documents("knowledge", where={"category": "面试"})
    assert stored
    assert "线程池" in stored[0].text
    assert stored[0].metadata["tags"] == '["interview", "java"]'


def test_local_importer_reimports_when_metadata_changes(
    tmp_path: Path,
    store: VectorStore,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "knowledge.txt").write_text("系统设计与缓存一致性", encoding="utf-8")
    importer = LocalKnowledgeImporter(store, state_path=tmp_path / "state.json")

    first = importer.import_directory(docs, collection="knowledge", tags=["system"])
    second = importer.import_directory(docs, collection="knowledge", tags=["architecture"])

    assert first.files_processed == 1
    assert second.files_processed == 1
    assert second.files_skipped == 0
    stored = store.get_documents("knowledge")
    assert len(stored) == 1
    assert stored[0].metadata["tags"] == '["architecture"]'


def test_scraping_settings_environment_overrides(monkeypatch) -> None:
    monkeypatch.setenv("OPENHARNESS_SCRAPING_ENABLED", "true")
    monkeypatch.setenv("OPENHARNESS_SCRAPING_PROXY_HTTP", "http://proxy.test:7890")
    monkeypatch.setenv("OPENHARNESS_SCRAPING_MAX_REQUESTS_PER_MINUTE", "6")
    monkeypatch.setenv("OPENHARNESS_SCRAPING_USER_AGENTS", "UA-1|UA-2")

    updated = _apply_env_overrides(Settings())

    assert updated.scraping.enabled is True
    assert updated.scraping.proxy_http == "http://proxy.test:7890"
    assert updated.scraping.max_requests_per_minute == 6
    assert updated.scraping.user_agents == ["UA-1", "UA-2"]
