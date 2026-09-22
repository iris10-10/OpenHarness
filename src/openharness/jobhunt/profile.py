"""Canonical user-profile structure and AI visibility rules.

The profile is the source of truth for personal/job-search information.  The
application settings model may still contain legacy copies for compatibility,
but new reads should go through this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

PROFILE_SECTIONS = (
    "basic",
    "skills",
    "education",
    "experience",
    "preferences",
    "job_search_status",
)

_DEFAULT_ALLOWED_SECTIONS = list(PROFILE_SECTIONS)
_BASIC_FIELDS = (
    "display_name",
    "current_city",
    "target_cities",
    "years_of_experience",
    "current_title",
    "current_salary",
    "name",
    "phone",
    "email",
    "address",
)
_SENSITIVE_BASIC_FIELDS = {"current_salary", "name", "display_name"}
_CONTACT_BASIC_FIELDS = {"phone", "email", "address"}
_KNOWN_SKILL_FIELDS = {"technical", "domains", "soft", "languages"}
_KNOWN_EDUCATION_FIELDS = {"school", "degree", "major", "graduation_year"}
_KNOWN_PREFERENCE_FIELDS = {
    "target_positions",
    "expected_salary_min",
    "expected_salary_max",
    "company_types",
    "work_mode",
    "accept_travel",
    "accept_overtime",
}


def default_profile_privacy() -> dict[str, Any]:
    """Return privacy defaults for a newly created profile."""
    return {
        "ai_enabled": True,
        "allowed_sections": list(_DEFAULT_ALLOWED_SECTIONS),
        "include_sensitive_fields": False,
        "include_contact_fields": False,
    }


def profile_with_defaults(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a defensive profile copy with a complete privacy block."""
    result = deepcopy(dict(profile or {}))
    privacy = result.get("privacy")
    defaults = default_profile_privacy()
    if isinstance(privacy, Mapping):
        defaults.update(dict(privacy))
    allowed = defaults.get("allowed_sections")
    if not isinstance(allowed, list):
        defaults["allowed_sections"] = list(_DEFAULT_ALLOWED_SECTIONS)
    else:
        defaults["allowed_sections"] = [
            str(item) for item in allowed if str(item) in PROFILE_SECTIONS
        ]
    result["privacy"] = defaults
    return result


def normalize_profile_updates(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize user-editable profile values without dropping legacy fields."""
    result = deepcopy(dict(updates))
    privacy = result.get("privacy")
    if isinstance(privacy, Mapping):
        normalized_privacy = dict(privacy)
        if "allowed_sections" in normalized_privacy:
            values = normalized_privacy["allowed_sections"]
            normalized_privacy["allowed_sections"] = [
                str(item) for item in values if str(item) in PROFILE_SECTIONS
            ] if isinstance(values, list) else list(_DEFAULT_ALLOWED_SECTIONS)
        result["privacy"] = normalized_privacy
    return result


def migrate_legacy_job_hunt_settings(
    profile: Mapping[str, Any] | None,
    settings: Any,
) -> dict[str, Any]:
    """Fill missing profile values from pre-profile job-hunt settings.

    Migration is intentionally additive: values already entered in
    ``profile.json`` always win.
    """
    job_hunt = getattr(settings, "job_hunt", None)
    if job_hunt is None:
        return dict(profile or {})

    raw_profile = dict(profile or {})
    if not raw_profile:
        legacy_values = (
            getattr(job_hunt, "target_cities", [])
            or getattr(job_hunt, "target_positions", [])
            or getattr(job_hunt, "expected_salary_min", None) is not None
            or getattr(job_hunt, "expected_salary_max", None) is not None
            or getattr(job_hunt, "years_of_experience", None) is not None
            or getattr(job_hunt, "default_company_types", [])
        )
        if not legacy_values:
            return {}

    result = profile_with_defaults(raw_profile)

    basic = dict(result.get("basic") or {})
    preferences = dict(result.get("preferences") or {})
    legacy_cities = list(getattr(job_hunt, "target_cities", []) or [])
    if not basic.get("target_cities") and legacy_cities:
        basic["target_cities"] = legacy_cities
    if basic.get("years_of_experience") is None:
        years = getattr(job_hunt, "years_of_experience", None)
        if years is not None:
            basic["years_of_experience"] = years
    legacy_positions = list(getattr(job_hunt, "target_positions", []) or [])
    if not preferences.get("target_positions") and legacy_positions:
        preferences["target_positions"] = legacy_positions
    if preferences.get("expected_salary_min") is None:
        salary_min = getattr(job_hunt, "expected_salary_min", None)
        if salary_min is not None:
            preferences["expected_salary_min"] = salary_min
    if preferences.get("expected_salary_max") is None:
        salary_max = getattr(job_hunt, "expected_salary_max", None)
        if salary_max is not None:
            preferences["expected_salary_max"] = salary_max
    legacy_company_types = list(getattr(job_hunt, "default_company_types", []) or [])
    if not preferences.get("company_types") and legacy_company_types:
        preferences["company_types"] = legacy_company_types
    if basic:
        result["basic"] = basic
    if preferences:
        result["preferences"] = preferences
    return result


def migrate_profile_store(store: Any, settings: Any) -> dict[str, Any]:
    """Migrate legacy settings into the profile store once, preserving edits."""
    current = store.load_profile()
    migrated = migrate_legacy_job_hunt_settings(current, settings)
    if migrated != current:
        store.save_profile(migrated)
    return migrated


def ai_visible_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only profile data allowed to enter an AI request."""
    source = profile_with_defaults(profile)
    privacy = source["privacy"]
    if not privacy.get("ai_enabled", True):
        return {}
    allowed = set(privacy.get("allowed_sections") or [])
    include_sensitive = bool(privacy.get("include_sensitive_fields"))
    include_contact = bool(privacy.get("include_contact_fields"))
    visible: dict[str, Any] = {}

    for section in PROFILE_SECTIONS:
        if section not in allowed or section not in source:
            continue
        value = source[section]
        if section == "basic" and isinstance(value, Mapping):
            filtered = {}
            for key in _BASIC_FIELDS:
                if key not in value:
                    continue
                if value[key] in (None, "", [], {}):
                    continue
                if key in _CONTACT_BASIC_FIELDS and not include_contact:
                    continue
                if key in _SENSITIVE_BASIC_FIELDS and not include_sensitive:
                    continue
                filtered[key] = deepcopy(value[key])
            if filtered:
                visible[section] = filtered
        elif section == "skills" and isinstance(value, Mapping):
            visible[section] = {
                key: deepcopy(item)
                for key, item in value.items()
                if key in _KNOWN_SKILL_FIELDS
            }
        elif section == "education" and isinstance(value, Mapping):
            visible[section] = {
                key: deepcopy(item)
                for key, item in value.items()
                if key in _KNOWN_EDUCATION_FIELDS
            }
        elif section == "preferences" and isinstance(value, Mapping):
            visible[section] = {
                key: deepcopy(item)
                for key, item in value.items()
                if key in _KNOWN_PREFERENCE_FIELDS
            }
        elif section == "experience" or section == "job_search_status":
            visible[section] = deepcopy(value)
    return {
        key: value
        for key, value in visible.items()
        if value not in (None, "", [], {})
    }


def ai_visibility_summary(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a UI/API-friendly summary of what the model may receive."""
    source = profile_with_defaults(profile)
    privacy = source["privacy"]
    visible = ai_visible_profile(source)
    excluded = [
        section for section in PROFILE_SECTIONS if section not in visible
    ]
    if not privacy.get("ai_enabled", True):
        reason = "用户画像不会自动发送给 AI"
    else:
        reason = "仅发送已允许的画像分区，联系方式默认不发送"
    return {
        "enabled": bool(privacy.get("ai_enabled", True)),
        "allowed_sections": list(privacy.get("allowed_sections") or []),
        "included_sections": list(visible),
        "excluded_sections": excluded,
        "include_sensitive_fields": bool(privacy.get("include_sensitive_fields")),
        "include_contact_fields": bool(privacy.get("include_contact_fields")),
        "reason": reason,
        "profile": visible,
    }
