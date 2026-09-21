"""RAG data source collectors (recruiting platforms, interview archives, local files)."""

from openharness.jobhunt.job_provider import (
    JobSearchQuery,
    JobSearchService,
    JobSyncLimits,
    JobSyncReport,
    McpJobsProvider,
    ReadOnlyJobsProvider,
)
from openharness.jobhunt.job_schema import (
    JobDataError,
    JobRecord,
    JobSchemaError,
    SourceRegistration,
    SourceRegistry,
    SourceValidationError,
    canonicalize_https_url,
    normalize_job_record,
    sanitize_external_text,
)
from openharness.rag.sources.base import BaseScraper, ScrapedDocument, ScraperConfig, ScrapeReport
from openharness.rag.sources.company_scraper import (
    CompanyInfo,
    GithubScraper,
    MaimaiScraper,
    TianyanchaScraper,
)
from openharness.rag.sources.interview_scraper import (
    InterviewExperience,
    LeetcodeScraper,
    NowcoderScraper,
)
from openharness.rag.sources.job_scraper import (
    BossScraper,
    JobPosting,
    JobScraperBase,
    LagouScraper,
)
from openharness.rag.sources.local_importer import LocalImportOptions, LocalKnowledgeImporter

__all__ = [
    "BaseScraper",
    "BossScraper",
    "CompanyInfo",
    "GithubScraper",
    "InterviewExperience",
    "JobDataError",
    "JobPosting",
    "JobRecord",
    "JobSchemaError",
    "JobScraperBase",
    "JobSearchQuery",
    "JobSearchService",
    "JobSyncLimits",
    "JobSyncReport",
    "LagouScraper",
    "LeetcodeScraper",
    "LocalImportOptions",
    "LocalKnowledgeImporter",
    "MaimaiScraper",
    "McpJobsProvider",
    "NowcoderScraper",
    "ReadOnlyJobsProvider",
    "ScrapeReport",
    "ScrapedDocument",
    "ScraperConfig",
    "SourceRegistration",
    "SourceRegistry",
    "SourceValidationError",
    "TianyanchaScraper",
    "canonicalize_https_url",
    "normalize_job_record",
    "sanitize_external_text",
]
