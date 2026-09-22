"""Career-path tool: career_path_plan.

Plans multiple long-term routes — 技术专家 / 技术管理 / 产品 / 创业 — from the
user's profile signals (title, years, skills, interests). Every route ships
three milestones (目标 / 需补技能 / 达成信号) and a renderable skill tree.

The route fit scores are an explicit heuristic: a transparent rule-based
score over the profile signals (兴趣 > 当前职位 > 技能), reported together
with the matched signals so the user can inspect and discount them. This is
decision support, not a career prediction; nothing about market outcomes is
fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from openharness.jobhunt.scoring import CandidateProfile
from openharness.tools.base import ToolExecutionContext, ToolResult
from openharness.tools.jobhunt_base import JobHuntToolBase, error_result, json_output

_AUTO_ROUTE = "自动"


@dataclass(frozen=True)
class _Stage:
    """One milestone inside a route."""

    name: str
    goals: tuple[str, ...]
    skills: tuple[str, ...]
    signals: tuple[str, ...]


_ROUTE_SUMMARIES: dict[str, str] = {
    "技术专家": "深耕技术纵深，以架构能力与领域积累建立不可替代性",
    "技术管理": "从个人贡献者转向团队与交付负责人，放大他人的产出",
    "产品": "转向产品方向，对用户价值与业务结果负责",
    "创业": "用最小成本验证自己的产品判断，承担全面风险与回报",
}

_ROUTES: dict[str, tuple[_Stage, ...]] = {
    "技术专家": (
        _Stage(
            name="阶段一 · 深度夯实",
            goals=(
                "独立负责一个核心模块/服务的完整迭代",
                "在性能、稳定性或工程效率上产出可量化改进",
            ),
            skills=("领域深耕（选定后端/大数据/算法等方向）", "性能调优与问题定位", "核心框架源码阅读"),
            signals=("能独立完成复杂需求的技术方案并落地", "有可量化的技术产出（性能/稳定性/效率指标）"),
        ),
        _Stage(
            name="阶段二 · 方案主导",
            goals=("主导跨模块技术方案设计与评审", "沉淀可复用的技术组件或规范"),
            skills=("架构设计（分层/解耦/扩展性）", "技术选型与权衡", "跨团队协作与表达"),
            signals=("技术方案被采纳并上线", "被同事依赖为技术咨询对象"),
        ),
        _Stage(
            name="阶段三 · 专家影响",
            goals=("负责复杂系统的整体架构与演进", "建立个人技术影响力（内部/社区/开源）"),
            skills=("高并发/高可用架构", "技术规划与前瞻调研", "写作与公开表达"),
            signals=("成为公司内某方向被点名咨询的专家", "有公开输出（博客/演讲/开源/专利）"),
        ),
    ),
    "技术管理": (
        _Stage(
            name="阶段一 · 角色过渡",
            goals=("在现有岗位上主动承担协调与带新人的职责", "完成从个人贡献者到交付责任人的视角转变"),
            skills=("任务拆解与节奏管理", "代码评审与传帮带", "向上沟通与汇报"),
            signals=("稳定带 1-2 名新人/实习生", "负责一个小组的迭代交付"),
        ),
        _Stage(
            name="阶段二 · 团队负责",
            goals=("正式承担小组/方向的团队管理", "建立可持续的协作机制（评审/复盘/排期）"),
            skills=("目标设定与绩效反馈", "招聘与面试", "跨团队项目协调"),
            signals=("团队交付稳定、人员流失可控", "能对上级清晰讲述团队目标与进展"),
        ),
        _Stage(
            name="阶段三 · 组织影响",
            goals=("负责多小组或一条业务线的人与事", "参与组织层面的规划与资源分配"),
            skills=("组织设计与人才梯队", "业务理解与成本意识", "冲突处理与决策"),
            signals=("被团队信任为最终决策者", "能培养出下一层负责人"),
        ),
    ),
    "产品": (
        _Stage(
            name="阶段一 · 认知补齐",
            goals=("系统理解目标业务的产品链路与用户场景", "在现有岗位上参与需求分析与方案讨论"),
            skills=("需求分析与用户访谈", "竞品分析与数据解读", "原型与文档表达"),
            signals=("能独立输出一份被采纳的需求文档", "与产品/运营建立稳定协作"),
        ),
        _Stage(
            name="阶段二 · 角色转型",
            goals=("内部转岗产品，或承担事实上的需求 owner", "独立负责一条小产品线的迭代"),
            skills=("产品规划与优先级", "指标体系建设（北极星/漏斗）", "跨职能推动落地"),
            signals=("转岗成功或成为事实上的需求负责人", "负责的迭代有可验证的用户/业务指标"),
        ),
        _Stage(
            name="阶段三 · 产品负责",
            goals=("负责完整产品线或业务方向", "平衡用户价值与商业目标"),
            skills=("商业理解与 ROI", "增长方法论", "团队与资源协调"),
            signals=("产品关键指标持续增长", "形成自己的产品判断并被团队认可"),
        ),
    ),
    "创业": (
        _Stage(
            name="阶段一 · 验证起步",
            goals=("把想法压缩成可验证的最小问题，访谈 3-5 个真实目标用户", "用业余时间做出最小可行产品（MVP）"),
            skills=("用户洞察与快速验证", "全栈/快速原型能力", "时间与精力管理"),
            signals=("有真实用户愿意使用或付费的迹象", "完成 1 个端到端小项目"),
        ),
        _Stage(
            name="阶段二 · 组队商业化",
            goals=("找到 1-2 个互补合伙人并明确分工", "跑通第一笔收入或清晰的增长路径"),
            skills=("团队搭建与股权常识", "获客与定价", "法务财务最小常识"),
            signals=("有持续的真实用户增长或首笔收入", "团队能按周推进关键指标"),
        ),
        _Stage(
            name="阶段三 · 放大或转型",
            goals=("决定放大（融资/扩张）或体面退出（关停/被收购）", "沉淀方法论与行业网络"),
            skills=("融资与资源整合", "组织搭建", "决策与止损能力"),
            signals=("业务进入自驱动的增长或完成有序退出", "无论成败都有可讲述的能力与案例"),
        ),
    ),
}

#信号词：兴趣命中 +10 / 当前职位命中 +8 / 技能命中 +3
_ROUTE_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "技术专家": ("架构", "技术", "专家", "深耕", "底层", "源码", "性能", "开源", "算法", "工程"),
    "技术管理": ("管理", "带团队", "带人", "团队", "协作", "统筹", "沟通", "负责人", "leader", "lead"),
    "产品": ("产品", "业务", "用户", "增长", "商业化", "需求", "运营", "转产品"),
    "创业": ("创业", "独立开发", "副业", "全栈", "从0到1", "自由职业"),
}


def _timeline_labels(horizon_years: int) -> tuple[str, str, str]:
    first = max(4, round(horizon_years * 12 / 3))
    second = first * 2
    return (f"0-{first} 个月", f"{first}-{second} 个月", f"{second} 个月+")


def _route_scores(
    *,
    interests: list[str],
    skills: list[str],
    current_title: str,
    years: float,
) -> dict[str, dict[str, Any]]:
    """Transparent rule-based fit scores with matched-signal evidence."""
    interests_text = " ".join(interests).lower()
    skills_text = " ".join(skills).lower()
    title_text = current_title.lower()
    results: dict[str, dict[str, Any]] = {}
    for route, keywords in _ROUTE_SIGNAL_KEYWORDS.items():
        score = 50.0
        matched: list[str] = []
        for keyword in keywords:
            if keyword in interests_text:
                score += 10
                matched.append(f"兴趣命中：{keyword}")
            if keyword in title_text:
                score += 8
                matched.append(f"当前职位命中：{keyword}")
            elif keyword in skills_text:
                score += 3
                matched.append(f"技能命中：{keyword}")
        if route == "技术专家":
            bonus = min(15.0, 3.0 * len(skills))
            if bonus:
                score += bonus
                matched.append(f"技术技能 {len(skills)} 项（+{bonus:g}）")
        elif route == "技术管理":
            if years >= 5:
                score += 8
                matched.append(f"{years:g} 年经验（+8）")
            elif years >= 3:
                score += 4
                matched.append(f"{years:g} 年经验（+4）")
        elif route == "创业":
            if len(skills) >= 6:
                score += 5
                matched.append(f"技能面较宽（{len(skills)} 项，+5）")
            else:
                score -= 5
                matched.append(f"技能面较窄（{len(skills)} 项，-5）")
        results[route] = {
            "fit_score": round(max(0.0, min(100.0, score))),
            "signals_matched": matched,
        }
    return results


def _skill_tree(
    route: str, stages: tuple[_Stage, ...], timeline: tuple[str, str, str]
) -> dict[str, Any]:
    """Skill tree for one route (text + structured nodes)."""
    header = route
    lines = [f"{header}（目标）"]
    for index, stage in enumerate(stages):
        connector = "└─" if index == len(stages) - 1 else "├─"
        lines.append(f"{connector} {stage.name}（{timeline[index]}）：{'、'.join(stage.skills)}")
    return {
        "root": header,
        "tree_text": "\n".join(lines),
        "nodes": [
            {
                "stage": stage.name,
                "timeline": timeline[index],
                "skills": list(stage.skills),
                "goals": list(stage.goals),
                "signals": list(stage.signals),
            }
            for index, stage in enumerate(stages)
        ],
    }


# ---------------------------------------------------------------------------
# career_path_plan
# ---------------------------------------------------------------------------


class CareerPathPlanToolInput(BaseModel):
    """Arguments for the career_path_plan tool."""

    current_title: str = Field(
        default="", description="Current title; falls back to the stored profile"
    )
    years_experience: float | None = Field(
        default=None, ge=0, le=60, description="Years of experience; falls back to the profile"
    )
    skills: list[str] = Field(
        default_factory=list, description="Current skills; falls back to the profile"
    )
    interests: list[str] = Field(
        default_factory=list, description="Interest keywords, e.g. 管理 / 产品 / 创业 / 架构"
    )
    target_route: str = Field(
        default="自动", description="自动 / 技术专家 / 技术管理 / 产品 / 创业"
    )
    horizon_years: int = Field(default=3, ge=1, le=5, description="Planning horizon in years")


class CareerPathPlanTool(JobHuntToolBase):
    """Plan 技术专家 / 技术管理 / 产品 / 创业 routes with milestones."""

    name = "career_path_plan"
    description = (
        "Plan multiple career routes — 技术专家 / 技术管理 / 产品 / 创业 — from the "
        "user profile (title, years, skills) plus interest keywords. Returns "
        "transparent heuristic fit scores with matched signals, three "
        "milestones per route (目标/技能/达成信号) and a renderable skill tree. "
        "Decision support, not a prediction. Read-only."
    )
    input_model = CareerPathPlanToolInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True

    async def execute(
        self, arguments: CareerPathPlanToolInput, context: ToolExecutionContext
    ) -> ToolResult:
        target = arguments.target_route.strip() or _AUTO_ROUTE
        if target != _AUTO_ROUTE and target not in _ROUTES:
            return error_result(
                f"未知路线 '{arguments.target_route}'：可选 "
                f"{_AUTO_ROUTE} / {' / '.join(_ROUTES)}"
            )

        profile = self.resolve_profile(context)
        candidate = CandidateProfile.from_profile_dict(profile) if profile else CandidateProfile()

        current_title = arguments.current_title.strip() or candidate.current_title
        years = (
            arguments.years_experience
            if arguments.years_experience is not None
            else (candidate.years_experience or 0.0)
        )
        input_skills = [skill.strip() for skill in arguments.skills if skill.strip()]
        skills: list[str] = []
        for skill in [*input_skills, *candidate.skills]:
            if skill and skill not in skills:
                skills.append(skill)
        interests = [item.strip() for item in arguments.interests if item.strip()]

        scores = _route_scores(
            interests=interests, skills=skills, current_title=current_title, years=years
        )
        ordered = sorted(_ROUTES, key=lambda route: (-scores[route]["fit_score"], list(_ROUTES).index(route)))
        if target != _AUTO_ROUTE:
            ordered.remove(target)
            ordered.insert(0, target)
        recommended = ordered[0]

        timeline = _timeline_labels(arguments.horizon_years)
        routes_payload: list[dict[str, Any]] = []
        for route in ordered:
            stages = _ROUTES[route]
            routes_payload.append(
                {
                    "route": route,
                    "summary": _ROUTE_SUMMARIES[route],
                    "fit_score": scores[route]["fit_score"],
                    "signals_matched": scores[route]["signals_matched"],
                    "milestones": [
                        {
                            "stage": stage.name,
                            "timeline": timeline[index],
                            "goals": list(stage.goals),
                            "skills_to_build": list(stage.skills),
                            "signals": list(stage.signals),
                        }
                        for index, stage in enumerate(stages)
                    ],
                    "skill_tree": _skill_tree(route, stages, timeline),
                }
            )

        notes = [
            (
                "适配度为启发式规则打分（兴趣 > 当前职位 > 技能），非职业预测；"
                "建议结合兴趣、生活阶段与市场机会自行取舍"
            ),
        ]
        if not profile and not input_skills and not current_title:
            notes.append("画像为空：建议先 profile_update，或直接传入 current_title / skills / interests")

        payload: dict[str, Any] = {
            "recommended_route": recommended,
            "user_selected": target != _AUTO_ROUTE,
            "profile": {
                "current_title": current_title,
                "years_experience": years,
                "skill_count": len(skills),
                "skills": skills[:15],
                "interests": interests,
                "horizon_years": arguments.horizon_years,
            },
            "route_scores": {
                route: scores[route]["fit_score"] for route in _ROUTES
            },
            "routes": routes_payload,
            "notes": notes,
        }
        return ToolResult(
            output=json_output(payload),
            metadata={
                "recommended_route": recommended,
                "top_score": scores[recommended]["fit_score"],
            },
        )
