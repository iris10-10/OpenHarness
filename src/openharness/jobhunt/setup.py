"""Interactive setup flow for the job-hunt CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from openharness.config.paths import get_config_file_path
from openharness.config.settings import Settings, load_settings, save_settings
from openharness.jobhunt.profile import migrate_profile_store
from openharness.jobhunt.storage import JobHuntStore, resolve_jobhunt_dir


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = ",".join(value)
    else:
        raw = value
    return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]


def _prompt_text(label: str, default: str = "") -> str:
    return str(typer.prompt(label, default=default, show_default=bool(default))).strip()


def _prompt_int(label: str, default: int | None = None) -> int | None:
    text = _prompt_text(label, "" if default is None else str(default))
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        typer.echo(f"忽略无效数字: {text}", err=True)
        return default


def _prompt_float(label: str, default: float | None = None) -> float | None:
    text = _prompt_text(label, "" if default is None else str(default))
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        typer.echo(f"忽略无效数字: {text}", err=True)
        return default


def _with_updated_settings(
    settings: Settings,
    *,
    rag_enabled: bool,
    embedding_provider: str,
    cities: list[str],
    positions: list[str],
    salary_min: int | None,
    salary_max: int | None,
    years: float | None,
    company_types: list[str],
    scraping_enabled: bool,
) -> Settings:
    return settings.model_copy(
        update={
            "rag": settings.rag.model_copy(
                update={
                    "enabled": rag_enabled,
                    "embedding": settings.rag.embedding.model_copy(
                        update={"provider": embedding_provider or settings.rag.embedding.provider}
                    ),
                }
            ),
            "job_hunt": settings.job_hunt.model_copy(
                update={
                    # Personal/job-search fields are now canonical in
                    # profile.json. Keep the legacy model for old config
                    # files, but do not create a second source of truth.
                    "target_cities": settings.job_hunt.target_cities,
                    "target_positions": settings.job_hunt.target_positions,
                    "expected_salary_min": settings.job_hunt.expected_salary_min,
                    "expected_salary_max": settings.job_hunt.expected_salary_max,
                    "years_of_experience": settings.job_hunt.years_of_experience,
                    "default_company_types": settings.job_hunt.default_company_types,
                }
            ),
            "scraping": settings.scraping.model_copy(update={"enabled": scraping_enabled}),
        }
    )


def _profile_updates(
    *,
    title: str,
    cities: list[str],
    positions: list[str],
    salary_min: int | None,
    salary_max: int | None,
    years: float | None,
    skills: list[str],
) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "basic": {
            "current_title": title or (positions[0] if positions else ""),
            "target_cities": cities,
            "years_of_experience": years,
        },
        "preferences": {
            "target_positions": positions,
            "expected_salary_min": salary_min,
            "expected_salary_max": salary_max,
        },
    }
    if skills:
        updates["skills"] = {"technical": skills}
    return updates


def run_setup(
    *,
    yes: bool = False,
    rag_enabled: bool | None = None,
    embedding_provider: str | None = None,
    cities: str | None = None,
    positions: str | None = None,
    salary_min: int | None = None,
    salary_max: int | None = None,
    years: float | None = None,
    company_types: str | None = None,
    skills: str | None = None,
    current_title: str | None = None,
    resume: Path | None = None,
    scraping_enabled: bool | None = None,
) -> dict[str, Any]:
    """Run the setup wizard and persist settings/profile updates."""
    settings = load_settings()
    initial_dir = resolve_jobhunt_dir(configured=settings.job_hunt.data_directory)
    existing_profile = migrate_profile_store(JobHuntStore(initial_dir), settings)
    basic = existing_profile.get("basic") if isinstance(existing_profile.get("basic"), dict) else {}
    preferences = (
        existing_profile.get("preferences")
        if isinstance(existing_profile.get("preferences"), dict)
        else {}
    )
    skills_block = (
        existing_profile.get("skills")
        if isinstance(existing_profile.get("skills"), dict)
        else {}
    )
    profile_cities = basic.get("target_cities") or settings.job_hunt.target_cities
    profile_positions = preferences.get("target_positions") or settings.job_hunt.target_positions
    profile_salary_min = (
        preferences.get("expected_salary_min")
        if preferences.get("expected_salary_min") is not None
        else settings.job_hunt.expected_salary_min
    )
    profile_salary_max = (
        preferences.get("expected_salary_max")
        if preferences.get("expected_salary_max") is not None
        else settings.job_hunt.expected_salary_max
    )
    profile_years = (
        basic.get("years_of_experience")
        if basic.get("years_of_experience") is not None
        else settings.job_hunt.years_of_experience
    )
    profile_company_types = (
        preferences.get("company_types") or settings.job_hunt.default_company_types
    )
    profile_title = basic.get("current_title") or ""
    typer.echo("OpenHarness Job Hunt setup")

    if yes:
        final_rag_enabled = settings.rag.enabled if rag_enabled is None else rag_enabled
        final_embedding = embedding_provider or settings.rag.embedding.provider
        final_cities = _split_csv(cities) or profile_cities
        final_positions = _split_csv(positions) or profile_positions
        final_salary_min = salary_min if salary_min is not None else profile_salary_min
        final_salary_max = salary_max if salary_max is not None else profile_salary_max
        final_years = years if years is not None else profile_years
        final_company_types = _split_csv(company_types) or profile_company_types
        final_skills = _split_csv(skills)
        final_title = current_title or profile_title or (
            final_positions[0] if final_positions else ""
        )
        final_scraping_enabled = (
            settings.scraping.enabled if scraping_enabled is None else scraping_enabled
        )
    else:
        typer.echo("1. 配置 RAG 与 Embedding")
        default_rag = settings.rag.enabled if rag_enabled is None else rag_enabled
        final_rag_enabled = typer.confirm("启用 RAG 上下文注入?", default=default_rag)
        final_embedding = _prompt_text(
            "Embedding provider (auto/openai/local/hash)",
            embedding_provider or settings.rag.embedding.provider,
        )
        typer.echo("2. 设置求职偏好")
        final_cities = _split_csv(
            cities
            if cities is not None
            else _prompt_text("目标城市，用逗号分隔", ",".join(profile_cities))
        )
        final_positions = _split_csv(
            positions
            if positions is not None
            else _prompt_text("目标岗位，用逗号分隔", ",".join(profile_positions))
        )
        final_salary_min = salary_min if salary_min is not None else _prompt_int(
            "期望最低月薪 K", profile_salary_min
        )
        final_salary_max = salary_max if salary_max is not None else _prompt_int(
            "期望最高月薪 K", profile_salary_max
        )
        final_years = years if years is not None else _prompt_float(
            "工作经验年限", profile_years
        )
        final_company_types = _split_csv(
            company_types
            if company_types is not None
            else _prompt_text("目标公司类型，用逗号分隔", ",".join(profile_company_types))
        )
        default_skills = (
            skills_block.get("technical")
            if isinstance(skills_block.get("technical"), list)
            else []
        )
        final_skills = _split_csv(
            skills
            if skills is not None
            else _prompt_text(
                "核心技能，用逗号分隔",
                ",".join(map(str, default_skills)),
            )
        )
        final_title = current_title if current_title is not None else _prompt_text(
            "当前/目标职位", profile_title or (final_positions[0] if final_positions else "")
        )
        final_scraping_enabled = (
            scraping_enabled
            if scraping_enabled is not None
            else typer.confirm(
                "启用招聘网站采集? 默认关闭，开启后网络请求仍按权限策略确认",
                default=False,
            )
        )

    updated = _with_updated_settings(
        settings,
        rag_enabled=final_rag_enabled,
        embedding_provider=final_embedding,
        cities=final_cities,
        positions=final_positions,
        salary_min=final_salary_min,
        salary_max=final_salary_max,
        years=final_years,
        company_types=final_company_types,
        scraping_enabled=final_scraping_enabled,
    )
    save_settings(updated)

    jobhunt_dir = resolve_jobhunt_dir(configured=updated.job_hunt.data_directory)
    store = JobHuntStore(jobhunt_dir)
    profile = store.merge_profile(
        _profile_updates(
            title=final_title,
            cities=final_cities,
            positions=final_positions,
            salary_min=final_salary_min,
            salary_max=final_salary_max,
            years=final_years,
            skills=final_skills,
        )
    )

    imported_resume = False
    if resume is not None:
        try:
            text = resume.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            typer.echo(f"简历读取失败: {exc}", err=True)
        else:
            from openharness.jobhunt.parsing import parse_resume_text

            parsed = parse_resume_text(text)
            resume_updates = parsed.to_dict()
            store.merge_profile(
                {
                    "resume": resume_updates,
                    "skills": {
                        "technical": parsed.technical_skills or final_skills,
                        "soft": parsed.soft_skills,
                        "languages": parsed.languages,
                    },
                }
            )
            imported_resume = True

    return {
        "settings_path": str(get_config_file_path()),
        "jobhunt_dir": str(jobhunt_dir),
        "rag_enabled": final_rag_enabled,
        "embedding_provider": final_embedding,
        "target_cities": final_cities,
        "target_positions": final_positions,
        "profile": profile,
        "resume_imported": imported_resume,
    }
