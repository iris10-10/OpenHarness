"""Tests for the career-path planning tool (透明启发式路线打分)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.jobhunt.storage import JobHuntStore
from openharness.tools.base import ToolResult
from openharness.tools.career_path_tool import CareerPathPlanTool, CareerPathPlanToolInput

EXPLICIT_SKILLS = ["Python", "FastAPI", "MySQL", "Redis", "Docker", "Kubernetes", "Git"]
TECH_MANAGE_TREE = (
    "技术管理（目标）\n"
    "├─ 阶段一 · 角色过渡（0-12 个月）：任务拆解与节奏管理、代码评审与传帮带、向上沟通与汇报\n"
    "├─ 阶段二 · 团队负责（12-24 个月）：目标设定与绩效反馈、招聘与面试、跨团队项目协调\n"
    "└─ 阶段三 · 组织影响（24 个月+）：组织设计与人才梯队、业务理解与成本意识、冲突处理与决策"
)


def _payload(result: ToolResult) -> dict[str, Any]:
    return json.loads(result.output)


@pytest.mark.asyncio
async def test_career_path_explicit_inputs(settings, ctx) -> None:
    result = await CareerPathPlanTool(settings=settings).execute(
        CareerPathPlanToolInput(
            current_title="Python 后端开发工程师",
            years_experience=6.5,
            skills=list(EXPLICIT_SKILLS),
            interests=["管理", "带团队"],
        ),
        ctx,
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["route_scores"] == {
        "技术专家": 73,
        "技术管理": 88,
        "产品": 50,
        "创业": 55,
    }
    assert payload["recommended_route"] == "技术管理"
    assert payload["user_selected"] is False

    routes = payload["routes"]
    assert [route["route"] for route in routes] == ["技术管理", "技术专家", "创业", "产品"]
    assert routes[0]["signals_matched"] == [
        "兴趣命中：管理",
        "兴趣命中：带团队",
        "兴趣命中：团队",
        "6.5 年经验（+8）",
    ]
    assert routes[1]["signals_matched"] == ["当前职位命中：工程", "技术技能 7 项（+15）"]
    assert routes[2]["signals_matched"] == ["技能面较宽（7 项，+5）"]
    assert routes[3]["signals_matched"] == []

    assert payload["profile"] == {
        "current_title": "Python 后端开发工程师",
        "years_experience": 6.5,
        "skill_count": 7,
        "skills": EXPLICIT_SKILLS,
        "interests": ["管理", "带团队"],
        "horizon_years": 3,
    }
    assert len(payload["notes"]) == 1
    assert payload["notes"][0].startswith("适配度为启发式规则打分")

    top = routes[0]
    assert top["fit_score"] == 88
    assert top["summary"] == "从个人贡献者转向团队与交付负责人，放大他人的产出"
    assert len(top["milestones"]) == 3
    assert top["milestones"][0] == {
        "stage": "阶段一 · 角色过渡",
        "timeline": "0-12 个月",
        "goals": [
            "在现有岗位上主动承担协调与带新人的职责",
            "完成从个人贡献者到交付责任人的视角转变",
        ],
        "skills_to_build": ["任务拆解与节奏管理", "代码评审与传帮带", "向上沟通与汇报"],
        "signals": ["稳定带 1-2 名新人/实习生", "负责一个小组的迭代交付"],
    }
    assert top["skill_tree"]["root"] == "技术管理"
    assert top["skill_tree"]["tree_text"] == TECH_MANAGE_TREE
    assert len(top["skill_tree"]["nodes"]) == 3
    assert result.metadata == {"recommended_route": "技术管理", "top_score": 88}


@pytest.mark.asyncio
async def test_career_path_profile_fallback(settings, ctx, profile_dict) -> None:
    JobHuntStore(ctx.cwd).save_profile(profile_dict)

    result = await CareerPathPlanTool(settings=settings).execute(CareerPathPlanToolInput(), ctx)

    assert result.is_error is False
    payload = _payload(result)
    assert payload["route_scores"] == {
        "技术专家": 73,
        "技术管理": 58,
        "产品": 50,
        "创业": 55,
    }
    assert payload["recommended_route"] == "技术专家"
    assert payload["user_selected"] is False

    assert payload["profile"] == {
        "current_title": "Python 后端开发工程师",
        "years_experience": 6.5,
        "skill_count": 6,
        "skills": ["Python", "FastAPI", "MySQL", "Redis", "Docker", "Kubernetes"],
        "interests": [],
        "horizon_years": 3,
    }
    assert len(payload["notes"]) == 1

    routes = payload["routes"]
    assert [route["route"] for route in routes] == ["技术专家", "技术管理", "创业", "产品"]
    assert routes[0]["signals_matched"] == ["当前职位命中：工程", "技术技能 6 项（+15）"]
    assert routes[1]["signals_matched"] == ["6.5 年经验（+8）"]
    assert result.metadata == {"recommended_route": "技术专家", "top_score": 73}


@pytest.mark.asyncio
async def test_career_path_empty_inputs_notes_and_selected_route(settings, ctx) -> None:
    tool = CareerPathPlanTool(settings=settings)

    empty = await tool.execute(CareerPathPlanToolInput(), ctx)
    payload = _payload(empty)
    assert payload["route_scores"] == {
        "技术专家": 50,
        "技术管理": 50,
        "产品": 50,
        "创业": 45,
    }
    assert payload["recommended_route"] == "技术专家"
    assert payload["user_selected"] is False
    assert payload["profile"]["skill_count"] == 0
    assert payload["notes"][1] == (
        "画像为空：建议先 profile_update，或直接传入 current_title / skills / interests"
    )
    assert empty.metadata == {"recommended_route": "技术专家", "top_score": 50}

    horizon = await tool.execute(CareerPathPlanToolInput(horizon_years=2), ctx)
    payload = _payload(horizon)
    assert [item["timeline"] for item in payload["routes"][0]["milestones"]] == [
        "0-8 个月",
        "8-16 个月",
        "16 个月+",
    ]

    selected = await tool.execute(CareerPathPlanToolInput(target_route="产品"), ctx)
    payload = _payload(selected)
    assert payload["user_selected"] is True
    assert payload["recommended_route"] == "产品"
    assert [route["route"] for route in payload["routes"]] == [
        "产品",
        "技术专家",
        "技术管理",
        "创业",
    ]
    assert payload["route_scores"]["产品"] == 50
    assert selected.metadata == {"recommended_route": "产品", "top_score": 50}

    invalid = await tool.execute(CareerPathPlanToolInput(target_route="创业家"), ctx)
    assert invalid.is_error is True
    assert "未知路线 '创业家'" in invalid.output
    assert "自动 / 技术专家 / 技术管理 / 产品 / 创业" in invalid.output


def test_career_path_read_only_flag() -> None:
    tool = CareerPathPlanTool()
    assert tool.is_read_only(CareerPathPlanToolInput()) is True
