"""Skill loading from bundled, user, compatibility, and project directories."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from openharness.config.paths import get_config_dir
from openharness.config.settings import load_settings
from openharness.skills._frontmatter import (
    optional_frontmatter_str,
    parse_bool_frontmatter,
    parse_skill_frontmatter,
    parse_skill_metadata,
)
from openharness.skills.bundled import get_bundled_skills
from openharness.skills.registry import SkillRegistry
from openharness.skills.types import SkillDefinition

logger = logging.getLogger(__name__)

_USER_COMPAT_SKILL_DIRS = (
    (".claude", "skills"),
    (".agents", "skills"),
)
_DEFAULT_PROJECT_SKILL_DIRS = (".openharness/skills", ".agents/skills", ".claude/skills")


def get_user_skills_dir() -> Path:
    """Return the OpenHarness user skills directory."""
    path = get_config_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_skill_dirs() -> list[Path]:
    """Return user-level skill directories loaded by default."""
    return [get_user_skills_dir(), *(Path.home().joinpath(*parts) for parts in _USER_COMPAT_SKILL_DIRS)]


#技能（Skill）加载器，负责从不同来源收集并注册技能
def load_skill_registry(
    cwd: str | Path | None = None,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings=None,
) -> SkillRegistry:
    """Load bundled, user-defined, project, and plugin skills."""
    registry = SkillRegistry()#创建一个空的技能注册器实例，用于后续存放所有加载到的技能
    for skill in get_bundled_skills():#加载捆绑技能
        registry.register(skill)
    for skill in load_user_skills():#加载用户技能（User Skills）
        registry.register(skill)
    for skill in load_skills_from_dirs(extra_skill_dirs, source="user"):#加载额外技能目录
        registry.register(skill)

    #确定最终使用的配置。
    resolved_settings = settings or load_settings()
    #这里的是项目skill
    if cwd is not None and getattr(resolved_settings, "allow_project_skills", True):
        project_dirs = discover_project_skill_dirs(
            cwd,
            getattr(resolved_settings, "project_skill_dirs", list(_DEFAULT_PROJECT_SKILL_DIRS)),
        )
        for skill in load_skills_from_dirs(project_dirs, source="project", create_missing=False):
            registry.register(skill)

    #加载插件里的skill
    if cwd is not None:
        from openharness.plugins.loader import load_plugins

        for plugin in load_plugins(resolved_settings, cwd, extra_roots=extra_plugin_roots):
            if not plugin.enabled:
                continue
            for skill in plugin.skills:
                registry.register(skill)
    return registry


def load_user_skills() -> list[SkillDefinition]:
    """Load markdown skills from user-level OpenHarness and compatibility directories."""
    return load_skills_from_dirs(get_user_skill_dirs(), source="user")


#从 cwd 开始向上查找到 Git 根目录，返回其中存在的项目技能目录
def discover_project_skill_dirs(
    cwd: str | Path,
    project_skill_dirs: Iterable[str] | None = None,
) -> list[Path]:
    """Return existing project skill directories from cwd up to the git root.

    Directories are ordered from least-specific to most-specific so later registry
    entries can override broader project or user skills deterministically.
    """
    start = Path(cwd).expanduser().resolve()
    if not start.exists():
        start = start.parent
    #是一个文件的话也要返回上一级，我们要的是目录
    if start.is_file():
        start = start.parent

    relative_dirs = _valid_project_skill_dirs(project_skill_dirs or _DEFAULT_PROJECT_SKILL_DIRS)
    git_root = _find_git_root(start)
    #获取当前用户的主目录
    home = Path.home().resolve()
    current = start
    #初始化空列表 levels，用于存储从 start 向上到停止点（Git 根或主目录或系统根）的所有目录层级
    levels: list[Path] = []
    while True:
        levels.append(current)
        if git_root is not None and current == git_root:
            break
        if git_root is None and current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    roots: list[Path] = []
    seen: set[Path] = set()
    for base in reversed(levels):
        for rel in relative_dirs:
            candidate = (base / rel).resolve()
            if candidate in seen or not candidate.is_dir():
                continue
            seen.add(candidate)
            roots.append(candidate)
    return roots


#返回安全的相对项目技能路径
def _valid_project_skill_dirs(project_skill_dirs: Iterable[str]) -> list[Path]:
    """Return safe relative project skill paths."""
    paths: list[Path] = []
    for raw in project_skill_dirs:
        value = str(raw).strip()
        if not value:
            continue
        rel = Path(value)
        #检查路径是否为绝对路径或者包含父目录引用
        if rel.is_absolute() or ".." in rel.parts:
            logger.warning("Ignoring unsafe project skill dir: %s", raw)
            continue
        paths.append(rel)
    return paths


#查找包含 start 的最近的 Git 仓库根目录，逐级向上
def _find_git_root(start: Path) -> Path | None:
    """Find the nearest git root containing start, if any."""
    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


#从一个或多个目录加载 Markdown 格式的技能
def load_skills_from_dirs(
    directories: Iterable[str | Path] | None,
    *,
    source: str = "user",
    create_missing: bool = True,#参数 create_missing：布尔类型，默认值为 True，表示如果传入的目录不存在，是否自动创建它。
) -> list[SkillDefinition]:
    """Load markdown skills from one or more directories.

    Supported layout:
    - ``<root>/<skill-dir>/SKILL.md``
    """
    skills: list[SkillDefinition] = []
    if not directories:
        return skills
    seen: set[Path] = set()
    for directory in directories:
        #得到路径
        root = Path(directory).expanduser().resolve()
        if create_missing:
            root.mkdir(parents=True, exist_ok=True)
        elif not root.is_dir():
            continue
        #初始化一个空列表 candidates，用于存放当前目录下所有有效的 SKILL.md 文件路径
        candidates: list[Path] = []
        for child in sorted(root.iterdir()):
            if child.is_dir():
                skill_path = child / "SKILL.md"
                if skill_path.exists():
                    candidates.append(skill_path)
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            content = path.read_text(encoding="utf-8")
            default_name = path.parent.name
            metadata = _parse_skill_metadata(default_name, content)
            name = metadata["name"]
            description = metadata["description"]
            display_name = name if name != default_name else None
            skills.append(
                SkillDefinition(
                    name=name,
                    description=description,
                    content=content,
                    source=source,
                    path=str(path),
                    base_dir=str(path.parent),
                    command_name=default_name,
                    display_name=display_name,
                    user_invocable=metadata["user_invocable"],
                    disable_model_invocation=metadata["disable_model_invocation"],
                    model=metadata["model"],
                    argument_hint=metadata["argument_hint"],
                )
            )
    return skills


def _parse_skill_markdown(default_name: str, content: str) -> tuple[str, str]:
    """Parse name and description from a skill markdown file with YAML frontmatter support."""
    return parse_skill_frontmatter(default_name, content, fallback_template="Skill: {name}")


def _parse_skill_metadata(default_name: str, content: str) -> dict:
    parsed = parse_skill_metadata(default_name, content, fallback_template="Skill: {name}")
    frontmatter = parsed.get("frontmatter")
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return {
        "name": str(parsed["name"]),
        "description": str(parsed["description"]),
        "user_invocable": parse_bool_frontmatter(frontmatter.get("user-invocable"), default=True),
        "disable_model_invocation": parse_bool_frontmatter(
            frontmatter.get("disable-model-invocation"),
            default=False,
        ),
        "model": optional_frontmatter_str(frontmatter.get("model")),
        "argument_hint": optional_frontmatter_str(frontmatter.get("argument-hint")),
    }
