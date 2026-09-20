"""Pydantic schemas shared by the job-hunt Web API routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str
    tools: list[dict[str, Any]] = Field(default_factory=list)


class ChatSendRequest(BaseModel):
    message: str
    session_id: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    cwd: str | None = None


class JobCreateRequest(BaseModel):
    title: str
    company: str
    city: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    experience: str = ""
    education: str = ""
    direction: str = ""
    company_type: str = ""
    tags: list[str] = Field(default_factory=list)
    jd_text: str = ""
    posted_date: str = ""
    url: str = ""


class JobSearchRequest(BaseModel):
    query: str = ""
    city: str = ""
    direction: str = ""
    company_type: str = ""
    salary_min: int | None = None
    page: int = 1
    page_size: int = 10


class JobMatchRequest(BaseModel):
    resume_text: str = ""
    job_ids: list[str] = Field(default_factory=list)


class ApplicationCreateRequest(BaseModel):
    company: str
    position: str
    channel: str = "其他"
    applied_date: str = ""
    status: str = "已投递"
    notes: str = ""
    job_id: str = ""
    tags: list[str] = Field(default_factory=list)


class ApplicationUpdateRequest(BaseModel):
    company: str | None = None
    position: str | None = None
    channel: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    follow_up_date: str | None = None


class ApplicationStatusRequest(BaseModel):
    status: str
    note: str = ""
    updated_date: str = ""


class ResumeGenerateRequest(BaseModel):
    jd_text: str
    resume_text: str | None = None
    template: str = "classic"


class ResumeOptimizeRequest(BaseModel):
    resume_text: str
    jd_text: str = ""


class InterviewPracticeRequest(BaseModel):
    company: str = ""
    position: str = ""
    round: str = "技术"
    jd_text: str = ""
    count: int = 8


class ProfileUpdateRequest(BaseModel):
    profile: dict[str, Any]
