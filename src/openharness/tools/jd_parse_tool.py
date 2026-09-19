"""JD intelligent parsing tool.

``jd_parse`` turns a free-form job description into the structured
:class:`~openharness.jobhunt.parsing.ParsedJD` model (title, company, city,
salary, experience / education requirements, required vs preferred skills,
responsibilities, benefits and role direction), reports the field-extraction
completeness and optionally ingests the posting into the RAG ``jobs``
collection for later retrieval and salary sampling.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.parsing import parse_jd_text
from openharness.jobhunt.storage import today_iso
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output


class JDParseToolInput(BaseModel):
    """Arguments for the jd_parse tool."""

    text: str = Field(description="Raw job-description text")
    url: str | None = Field(default=None, description="Source URL of the posting")
    title: str | None = Field(default=None, description="Job title hint")
    company: str | None = Field(default=None, description="Company name hint")
    city: str | None = Field(default=None, description="City hint, e.g. 北京 / 上海")
    posted_date: str | None = Field(
        default=None, description="Posting date (YYYY-MM-DD) for freshness scoring"
    )
    store_to_rag: bool = Field(
        default=True,
        description="Ingest the parsed JD into the RAG 'jobs' collection",
    )


class JDParseTool(JobHuntToolBase):
    """Parse a job description into the structured model."""

    name = "jd_parse"
    description = (
        "Parse a raw job description into structured fields: title, company, city, "
        "salary range, experience / education requirements, required and preferred "
        "skills, responsibilities, benefits and role direction, with a "
        "field-extraction completeness ratio. Optionally ingests the posting into "
        "the RAG 'jobs' collection. Writes to the local RAG store when store_to_rag "
        "is true."
    )
    input_model = JDParseToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        if isinstance(arguments, JDParseToolInput):
            return not arguments.store_to_rag
        return False

    async def execute(
        self, arguments: JDParseToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        del context
        if not arguments.text.strip():
            return error_result("'text' must be non-empty")

        jd = parse_jd_text(
            arguments.text,
            title_hint=arguments.title or "",
            company_hint=arguments.company or "",
            city_hint=arguments.city or "",
            url=arguments.url or "",
            posted_date=arguments.posted_date or "",
        )
        payload: dict[str, Any] = {
            "jd": jd.to_dict(),
            "completeness": jd.completeness,
        }

        warnings: list[str] = []
        storage: dict[str, Any] = {"stored": False}
        if arguments.store_to_rag:
            metadata: dict[str, Any] = {
                "kind": "job",
                "source": "jd_parse",
                "title": jd.title,
                "company": jd.company,
                "city": jd.city,
                "experience": jd.experience,
                "education": jd.education,
                "category": jd.category or "",
                "posted_date": jd.posted_date,
                "url": jd.url,
                "required_skills": list(jd.required_skills),
                "preferred_skills": list(jd.preferred_skills),
                "ingested_at": today_iso(),
            }
            if jd.salary is not None:
                metadata["salary_min"] = jd.salary.min_monthly
                metadata["salary_max"] = jd.salary.max_monthly
                metadata["months_per_year"] = jd.salary.months_per_year
            metadata = {key: value for key, value in metadata.items() if value not in ("", None)}
            try:
                doc_id = self.ingest_document(
                    collection="jobs", text=arguments.text, metadata=metadata
                )
                storage = {"stored": True, "collection": "jobs", "doc_id": doc_id}
            #RAG 不可用时解析结果仍然有效，不中断解析流程
            except Exception as exc:  # noqa: BLE001
                storage = {"stored": False, "error": str(exc)}
        if jd.completeness < 0.75:
            warnings.append(
                f"字段完整率为 {jd.completeness:.0%}，建议补充缺失字段（可传 title/company/city 提示参数）"
            )
        payload["storage"] = storage
        payload["warnings"] = warnings
        return ToolResult(
            output=json_output(payload),
            metadata={"completeness": jd.completeness, "category": jd.category or ""},
        )
