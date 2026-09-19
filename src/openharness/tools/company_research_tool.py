"""Company research tools (Phase 2, read-only).

Four thin research tools over the local RAG store; nothing is fabricated —
every answer cites stored documents and degrades to actionable notes when
the knowledge base is empty:

- ``company_search``: company profile snippets + a summary of its ingested
  job postings (count, titles, tech keywords).
- ``company_culture``: team atmosphere / work intensity, drawing on the
  ``companies`` and ``interview`` collections.
- ``company_techstack``: deterministic tech-stack ranking aggregated from
  the company's ingested job postings.
- ``company_history``: funding / scale / milestone snippets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import extract_skills
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

if TYPE_CHECKING:
    from openharness.rag.retriever import RankedHit

_EXCERPT_LIMIT = 320
_MAX_JOBS_FETCH = 50

_INGEST_HINT = "可先用 rag 摄入管道导入公司资料，或将该公司 JD 用 jd_parse 入库"


def _excerpt(text: str, limit: int = _EXCERPT_LIMIT) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _snippet_payload(hit: RankedHit) -> dict[str, Any]:
    return {
        "doc_id": hit.id,
        "collection": hit.collection,
        "company": str(hit.metadata.get("company", hit.metadata.get("name", ""))),
        "excerpt": _excerpt(hit.text),
        "score": round(float(hit.score), 4),
    }


def _skills_from_hit(hit: RankedHit) -> list[str]:
    metadata = hit.metadata
    skills = [
        *_as_str_list(metadata.get("required_skills")),
        *_as_str_list(metadata.get("preferred_skills")),
    ]
    if not skills:
        skills = extract_skills(hit.text)
    return skills


def _tech_stack(hits: list[RankedHit]) -> list[dict[str, Any]]:
    """Rank skills by the number of postings mentioning them (deterministic)."""
    counts: dict[str, int] = {}
    for hit in hits:
        for skill in dict.fromkeys(_skills_from_hit(hit)):
            counts[skill] = counts.get(skill, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"skill": skill, "count": count, "share": round(count / len(hits), 2)}
        for skill, count in ordered[:15]
    ]


class _CompanyToolBase(JobHuntToolBase):
    """Shared retrieval plumbing for the company research tools."""

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def _retrieve(
        self,
        *,
        query: str,
        collection: str | None = None,
        collections: list[str] | None = None,
        where: dict[str, Any] | None = None,
        top_n: int = 6,
    ) -> tuple[list[RankedHit], list[str]]:
        """Retrieve hits; construction/retrieval errors degrade to notes."""
        try:
            retriever = self.resolve_retriever()
            outcome = await retriever.retrieve(
                query,
                collection=collection,
                collections=collections,
                where=where,
                top_n=top_n,
            )
        #知识库不可用时返回提示而不是报错，研究工作仍可继续
        except Exception as exc:  # noqa: BLE001
            return [], [f"本地知识库检索不可用：{exc}"]
        return list(outcome.hits), [str(note) for note in outcome.notes]

    async def _company_jobs(self, company: str) -> tuple[list[RankedHit], list[str]]:
        """Fetch the company's ingested job postings from the jobs collection."""
        return await self._retrieve(
            query=f"{company} 岗位 JD",
            collection="jobs",
            where={"company": company},
            top_n=_MAX_JOBS_FETCH,
        )


# ---------------------------------------------------------------------------
# company_search
# ---------------------------------------------------------------------------


class CompanySearchToolInput(BaseModel):
    """Arguments for the company_search tool."""

    company: str = Field(description="Company name, e.g. 字节跳动")
    top_k: int = Field(default=5, ge=1, le=10, description="Maximum profile snippets")


class CompanySearchTool(_CompanyToolBase):
    """Search stored company profiles plus its ingested job postings."""

    name = "company_search"
    description = (
        "Research a company from the local knowledge base: profile snippets from "
        "the RAG 'companies' collection plus a summary of its ingested job "
        "postings (count, open titles, top tech keywords). Read-only; cites "
        "stored documents only."
    )
    input_model = CompanySearchToolInput

    async def execute(
        self, arguments: CompanySearchToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        company = arguments.company.strip()
        if not company:
            return error_result("'company' 为必填")

        snippets, notes = await self._retrieve(
            query=f"{company} 公司简介 主营业务 融资 规模 行业地位",
            collection="companies",
            top_n=arguments.top_k,
        )
        jobs, job_notes = await self._company_jobs(company)
        notes.extend(job_notes)

        titles: list[str] = []
        for hit in jobs:
            title = str(hit.metadata.get("title", "")).strip()
            if title and title not in titles:
                titles.append(title)
        payload: dict[str, Any] = {
            "company": company,
            "profile": {
                "snippet_count": len(snippets),
                "snippets": [_snippet_payload(hit) for hit in snippets],
            },
            "postings": {
                "count": len(jobs),
                "titles": titles[:8],
                "tech_stack": _tech_stack(jobs),
            },
        }
        if not snippets and not jobs:
            notes.append(f"知识库中没有 '{company}' 的资料：{_INGEST_HINT}")
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"snippet_count": len(snippets), "posting_count": len(jobs)},
        )


# ---------------------------------------------------------------------------
# company_culture
# ---------------------------------------------------------------------------


class CompanyCultureToolInput(BaseModel):
    """Arguments for the company_culture tool."""

    company: str = Field(description="Company name, e.g. 字节跳动")
    top_k: int = Field(default=5, ge=1, le=10, description="Maximum snippets")


class CompanyCultureTool(_CompanyToolBase):
    """Search culture / work-intensity references for a company."""

    name = "company_culture"
    description = (
        "Research a company's team atmosphere and work intensity from the local "
        "knowledge base (companies + interview experiences): snippets on 加班强度 / "
        "管理风格 / 员工评价, with source citations. Read-only."
    )
    input_model = CompanyCultureToolInput

    async def execute(
        self, arguments: CompanyCultureToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        company = arguments.company.strip()
        if not company:
            return error_result("'company' 为必填")

        hits, notes = await self._retrieve(
            query=f"{company} 团队氛围 加班强度 管理风格 员工评价 工作体验",
            collections=["companies", "interview"],
            top_n=arguments.top_k,
        )
        payload: dict[str, Any] = {
            "company": company,
            "snippet_count": len(hits),
            "snippets": [_snippet_payload(hit) for hit in hits],
        }
        if not hits:
            notes.append(
                f"知识库中没有 '{company}' 的文化/评价资料：{_INGEST_HINT}"
                "（也可导入面经、员工评价等文本）"
            )
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"snippet_count": len(hits)},
        )


# ---------------------------------------------------------------------------
# company_techstack
# ---------------------------------------------------------------------------


class CompanyTechstackToolInput(BaseModel):
    """Arguments for the company_techstack tool."""

    company: str = Field(description="Company name, e.g. 字节跳动")
    top_k: int = Field(default=10, ge=1, le=20, description="Maximum tech-stack entries")


class CompanyTechstackTool(_CompanyToolBase):
    """Rank a company's tech stack from its ingested job postings."""

    name = "company_techstack"
    description = (
        "Rank a company's technology stack deterministically from its ingested "
        "job postings: each skill is counted across the postings that mention it, "
        "with share ratios and sample posting titles. Read-only; ingest the "
        "company's JDs via jd_parse first."
    )
    input_model = CompanyTechstackToolInput

    async def execute(
        self, arguments: CompanyTechstackToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        company = arguments.company.strip()
        if not company:
            return error_result("'company' 为必填")

        jobs, notes = await self._company_jobs(company)
        samples = [
            {
                "doc_id": hit.id,
                "title": str(hit.metadata.get("title", "")),
                "city": str(hit.metadata.get("city", "")),
            }
            for hit in jobs[:5]
        ]
        payload: dict[str, Any] = {
            "company": company,
            "posting_count": len(jobs),
            "tech_stack": _tech_stack(jobs)[: arguments.top_k],
            "samples": samples,
        }
        if not jobs:
            notes.append(f"没有 '{company}' 的已入库岗位：{_INGEST_HINT}")
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"posting_count": len(jobs)},
        )


# ---------------------------------------------------------------------------
# company_history
# ---------------------------------------------------------------------------


class CompanyHistoryToolInput(BaseModel):
    """Arguments for the company_history tool."""

    company: str = Field(description="Company name, e.g. 字节跳动")
    top_k: int = Field(default=5, ge=1, le=10, description="Maximum snippets")


class CompanyHistoryTool(_CompanyToolBase):
    """Search funding / scale / milestone references for a company."""

    name = "company_history"
    description = (
        "Research a company's history from the local knowledge base: funding "
        "rounds, scale and milestones, with source citations. Read-only."
    )
    input_model = CompanyHistoryToolInput

    async def execute(
        self, arguments: CompanyHistoryToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        company = arguments.company.strip()
        if not company:
            return error_result("'company' 为必填")

        hits, notes = await self._retrieve(
            query=f"{company} 公司 融资历程 创始人 发展历史 规模 里程碑",
            collection="companies",
            top_n=arguments.top_k,
        )
        payload: dict[str, Any] = {
            "company": company,
            "snippet_count": len(hits),
            "snippets": [_snippet_payload(hit) for hit in hits],
        }
        if not hits:
            notes.append(f"知识库中没有 '{company}' 的发展历程资料：{_INGEST_HINT}")
        if notes:
            payload["notes"] = notes
        return ToolResult(
            output=json_output(payload),
            metadata={"snippet_count": len(hits)},
        )
