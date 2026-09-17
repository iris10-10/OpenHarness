"""Skill registry."""
#技能注册表
#用于管理“技能”注册表的模块

from __future__ import annotations

from openharness.skills.types import SkillDefinition


class SkillRegistry:
    """Store loaded skills by name."""
    #按名称存储已加载的技能

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        """Register one skill."""
        for key in (skill.name, skill.command_name, skill.display_name, *skill.aliases):
            if key:
                self._skills[key] = skill

    def get(self, name: str) -> SkillDefinition | None:
        """Return a skill by name."""
        return self._skills.get(name)

    #返回所有已注册技能的列表，按名称排序，且去重
    def list_skills(self) -> list[SkillDefinition]:
        """Return all skills sorted by name."""
        unique: dict[tuple[str, str | None], SkillDefinition] = {}
        for skill in self._skills.values():
            unique[(skill.source, skill.path or skill.name)] = skill
        return sorted(unique.values(), key=lambda skill: skill.command_name or skill.name)
