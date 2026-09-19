"""Deterministic text parsing for resumes and job descriptions (Phase 2).

Everything in this module is pure (no I/O, no network, no LLM calls) so the
job-hunt tools stay fast, private and unit-testable:

- Shared vocabulary: tech skills with alias canonicalisation and related
  families, major cities, degree levels, soft skills, languages.
- Primitive parsers: salary ranges, experience requirements, education
  requirements, date periods, contact info.
- Structured parsers: :func:`parse_resume_text` and :func:`parse_jd_text`
  turn free-form Chinese resume / JD text into dataclasses that the matching
  and gap-analysis engines consume.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MAJOR_CITIES",
    "ParsedJD",
    "ParsedResume",
    "SalaryRange",
    "are_skills_related",
    "classify_direction",
    "count_quantified_lines",
    "count_quantified_tokens",
    "degree_level",
    "detect_cities",
    "experience_bounds",
    "extract_contact",
    "extract_periods",
    "extract_skills",
    "extract_soft_skills",
    "guess_years_of_experience",
    "normalize_skill",
    "parse_education",
    "parse_experience",
    "parse_jd_text",
    "parse_resume_text",
    "parse_salary",
    "split_sections",
]

# ---------------------------------------------------------------------------
# 技能词表：显示名 -> 别名（别名一律小写，CJK 别名按子串匹配，ASCII 按词边界）
# ---------------------------------------------------------------------------

SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    # 编程语言
    "Python": ("python",),
    "Java": ("java",),
    "Go": ("golang", "go语言"),
    "C++": ("c++", "cpp"),
    "C语言": ("c语言",),
    "C#": ("c#", "csharp"),
    "JavaScript": ("javascript", "js", "es6"),
    "TypeScript": ("typescript", "ts"),
    "PHP": ("php",),
    "Ruby": ("ruby",),
    "Rust": ("rust",),
    "Kotlin": ("kotlin",),
    "Swift": ("swift",),
    "Scala": ("scala",),
    "Shell": ("shell", "bash"),
    "SQL": ("sql",),
    # Web / 前端
    "HTML": ("html", "html5"),
    "CSS": ("css", "css3"),
    "React": ("react", "react.js", "reactjs"),
    "Vue": ("vue", "vue.js", "vuejs"),
    "Angular": ("angular",),
    "Node.js": ("node.js", "nodejs", "node"),
    "Webpack": ("webpack",),
    "Vite": ("vite",),
    # Python 生态
    "Django": ("django",),
    "Flask": ("flask",),
    "FastAPI": ("fastapi",),
    "Celery": ("celery",),
    "Pydantic": ("pydantic",),
    # Java 生态
    "Spring": ("spring", "spring boot", "springboot", "spring cloud"),
    "MyBatis": ("mybatis",),
    "Hibernate": ("hibernate",),
    "JVM": ("jvm",),
    "Netty": ("netty",),
    "Dubbo": ("dubbo",),
    # 数据库 / 存储
    "MySQL": ("mysql",),
    "PostgreSQL": ("postgresql", "postgres"),
    "Oracle": ("oracle",),
    "SQLite": ("sqlite",),
    "MongoDB": ("mongodb", "mongo"),
    "Redis": ("redis",),
    "Memcached": ("memcached",),
    "Elasticsearch": ("elasticsearch", "elastic search"),
    "ClickHouse": ("clickhouse",),
    "HBase": ("hbase",),
    # 消息队列 / 中间件
    "Kafka": ("kafka",),
    "RabbitMQ": ("rabbitmq",),
    "RocketMQ": ("rocketmq",),
    "Zookeeper": ("zookeeper", "zk"),
    "Nginx": ("nginx",),
    # 云原生 / 运维
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "Linux": ("linux",),
    "Git": ("git",),
    "Jenkins": ("jenkins",),
    "CI/CD": ("ci/cd", "cicd", "持续集成", "持续交付"),
    "DevOps": ("devops",),
    "Prometheus": ("prometheus",),
    "Grafana": ("grafana",),
    "Terraform": ("terraform",),
    "AWS": ("aws", "亚马逊云"),
    "阿里云": ("阿里云",),
    "腾讯云": ("腾讯云",),
    "华为云": ("华为云",),
    "云原生": ("云原生", "cloud native"),
    # 大数据
    "Hadoop": ("hadoop",),
    "Spark": ("spark",),
    "Flink": ("flink",),
    "Hive": ("hive",),
    "HDFS": ("hdfs",),
    "Airflow": ("airflow",),
    "DataX": ("datax",),
    # 数据 / AI
    "Numpy": ("numpy",),
    "Pandas": ("pandas",),
    "PyTorch": ("pytorch",),
    "TensorFlow": ("tensorflow",),
    "机器学习": ("机器学习", "machine learning"),
    "深度学习": ("深度学习", "deep learning"),
    "NLP": ("nlp", "自然语言处理"),
    "计算机视觉": ("计算机视觉", "cv算法"),
    "大模型": ("大模型", "llm", "aigc"),
    "推荐系统": ("推荐系统", "推荐算法"),
    "数据分析": ("数据分析",),
    "数据挖掘": ("数据挖掘",),
    "数据仓库": ("数据仓库", "数仓"),
    "ETL": ("etl",),
    "Excel": ("excel",),
    "Tableau": ("tableau",),
    "PowerBI": ("power bi", "powerbi"),
    # 工程能力 / 方法论
    "微服务": ("微服务", "microservice"),
    "分布式系统": ("分布式",),
    "高并发": ("高并发",),
    "高可用": ("高可用",),
    "性能优化": ("性能优化", "性能调优"),
    "系统设计": ("系统设计", "架构设计"),
    "RESTful": ("restful", "rest api"),
    "gRPC": ("grpc",),
    "单元测试": ("单元测试", "unit test"),
    "自动化测试": ("自动化测试",),
    "Selenium": ("selenium",),
    "Pytest": ("pytest",),
    "JUnit": ("junit",),
    "Postman": ("postman",),
    # 移动端
    "Android": ("android",),
    "iOS": ("ios",),
    "Flutter": ("flutter",),
    "React Native": ("react native",),
    # 产品 / 运营 / 设计
    "产品设计": ("产品设计", "产品规划"),
    "需求分析": ("需求分析",),
    "原型设计": ("原型设计", "axure", "墨刀"),
    "用户增长": ("用户增长",),
    "活动策划": ("活动策划",),
    "项目管理": ("项目管理", "pmp"),
    "Scrum": ("scrum", "敏捷开发"),
    "UI设计": ("ui设计", "交互设计", "视觉设计", "figma", "sketch"),
    # 安全
    "网络安全": ("网络安全", "信息安全"),
    "渗透测试": ("渗透测试",),
    # 通用软技能（出现在 JD 要求或简历技能栏）
    "沟通能力": ("沟通能力", "沟通协调"),
    "团队协作": ("团队协作", "团队合作", "跨团队"),
    "学习能力": ("学习能力", "快速学习"),
    "抗压能力": ("抗压能力", "抗压性强"),
    "责任心": ("责任心", "owner意识", "owner 意识"),
    "领导力": ("领导力",),
    "执行力": ("执行力",),
}

#技能族：同族技能在差距分析中按“部分匹配”计算
SKILL_RELATED_GROUPS: tuple[tuple[str, ...], ...] = (
    ("JavaScript", "TypeScript", "Node.js", "React", "Vue", "Angular", "Webpack", "Vite"),
    ("Django", "Flask", "FastAPI"),
    ("Spring", "MyBatis", "Hibernate", "JVM", "Netty", "Dubbo"),
    ("MySQL", "PostgreSQL", "Oracle", "SQLite", "SQL"),
    ("Redis", "Memcached"),
    ("Elasticsearch", "ClickHouse", "MongoDB", "HBase"),
    ("Kafka", "RabbitMQ", "RocketMQ"),
    ("Docker", "Kubernetes", "云原生"),
    ("Jenkins", "CI/CD", "DevOps", "Git"),
    ("Prometheus", "Grafana", "Terraform"),
    ("AWS", "阿里云", "腾讯云", "华为云"),
    ("Hadoop", "Spark", "Flink", "Hive", "HDFS"),
    ("机器学习", "深度学习", "NLP", "计算机视觉", "大模型", "推荐系统", "PyTorch", "TensorFlow"),
    ("Numpy", "Pandas"),
    ("数据分析", "数据挖掘", "数据仓库", "ETL", "Excel", "Tableau", "PowerBI"),
    ("Selenium", "Pytest", "JUnit", "自动化测试", "单元测试"),
    ("Android", "iOS", "Flutter", "React Native"),
    ("产品设计", "需求分析", "原型设计", "项目管理", "Scrum"),
    ("用户增长", "活动策划"),
)

_SKILL_FAMILY_OF: dict[str, int] = {}
for _family_index, _family in enumerate(SKILL_RELATED_GROUPS):
    for _skill in _family:
        _SKILL_FAMILY_OF[_skill] = _family_index

#软技能词表：从简历中识别软技能
SOFT_SKILLS: tuple[str, ...] = (
    "沟通能力",
    "团队协作",
    "学习能力",
    "抗压能力",
    "责任心",
    "领导力",
    "执行力",
    "项目管理",
    "组织协调",
)

LANGUAGE_KEYWORDS: tuple[str, ...] = (
    "英语",
    "日语",
    "韩语",
    "法语",
    "德语",
    "俄语",
    "西班牙语",
    "CET-4",
    "CET-6",
    "雅思",
    "托福",
    "专业八级",
    "专业四级",
    "英语六级",
    "英语四级",
)

_SOFT_SKILL_SET: frozenset[str] = frozenset(SOFT_SKILLS)

MAJOR_CITIES: tuple[str, ...] = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "成都",
    "南京",
    "武汉",
    "西安",
    "苏州",
    "天津",
    "重庆",
    "长沙",
    "郑州",
    "合肥",
    "济南",
    "青岛",
    "大连",
    "厦门",
    "福州",
    "宁波",
    "无锡",
    "东莞",
    "佛山",
    "珠海",
    "昆明",
    "哈尔滨",
    "沈阳",
    "长春",
    "石家庄",
    "南昌",
    "贵阳",
    "南宁",
    "太原",
    "兰州",
    "乌鲁木齐",
    "呼和浩特",
    "银川",
    "西宁",
    "海口",
    "三亚",
    "香港",
    "澳门",
    "台北",
    "远程",
)

_DEGREE_LEVELS: dict[str, int] = {
    "不限": 0,
    "无要求": 0,
    "高中": 0,
    "中专": 0,
    "大专": 1,
    "专科": 1,
    "本科": 2,
    "学士": 2,
    "硕士": 3,
    "研究生": 3,
    "博士": 4,
}

# ---------------------------------------------------------------------------
# 技能匹配
# ---------------------------------------------------------------------------

_ASCII_ALIAS_RE: dict[str, re.Pattern[str]] = {}


def _alias_pattern(alias: str) -> re.Pattern[str] | None:
    """Return a compiled word-boundary regex for an ASCII alias.

    CJK aliases return ``None`` and are matched with a plain substring test.
    """
    if any("\u4e00" <= ch <= "\u9fff" for ch in alias):
        return None
    cached = _ASCII_ALIAS_RE.get(alias)
    if cached is None:
        cached = re.compile(rf"(?<![a-z0-9+#]){re.escape(alias)}(?![a-z0-9+#])")
        _ASCII_ALIAS_RE[alias] = cached
    return cached


def _skill_matches(display: str, aliases: Iterable[str], lowered: str) -> bool:
    for alias in aliases:
        pattern = _alias_pattern(alias)
        if pattern is None:
            if alias in lowered:
                return True
        elif pattern.search(lowered):
            return True
    return False


def extract_skills(text: str) -> list[str]:
    """Return canonical skill names found in ``text`` (ordered by position)."""
    if not text:
        return []
    lowered = text.lower()
    found: list[tuple[int, str]] = []
    for display, aliases in SKILL_ALIASES.items():
        best: int | None = None
        for alias in aliases:
            pattern = _alias_pattern(alias)
            if pattern is None:
                index = lowered.find(alias)
            else:
                match = pattern.search(lowered)
                index = match.start() if match else -1
            if index >= 0 and (best is None or index < best):
                best = index
        if best is not None:
            found.append((best, display))
    found.sort(key=lambda item: (item[0], item[1]))
    return [display for _, display in found]


def extract_soft_skills(text: str) -> list[str]:
    """Return soft-skill keywords found in ``text`` (ordered by position)."""
    if not text:
        return []
    lowered = text.lower()
    found: list[tuple[int, str]] = []
    for skill in SOFT_SKILLS:
        index = lowered.find(skill.lower())
        if index >= 0:
            found.append((index, skill))
    found.sort(key=lambda item: (item[0], item[1]))
    return [skill for _, skill in found]


def extract_languages(text: str) -> list[str]:
    """Return language-related keywords found in ``text``."""
    if not text:
        return []
    lowered = text.lower()
    found: list[tuple[int, str]] = []
    for keyword in LANGUAGE_KEYWORDS:
        index = lowered.find(keyword.lower())
        if index >= 0:
            found.append((index, keyword))
    found.sort(key=lambda item: (item[0], item[1]))
    return [keyword for _, keyword in found]


def normalize_skill(skill: str) -> str:
    """Canonicalise a skill name via the alias table (returns input when unknown)."""
    lowered = skill.strip().lower()
    for display, aliases in SKILL_ALIASES.items():
        if lowered == display.lower():
            return display
        if _skill_matches(display, aliases, lowered):
            return display
    return skill.strip()


def are_skills_related(left: str, right: str) -> bool:
    """Return True when two canonical skills belong to the same family."""
    left_family = _SKILL_FAMILY_OF.get(left)
    right_family = _SKILL_FAMILY_OF.get(right)
    return left_family is not None and left_family == right_family


# ---------------------------------------------------------------------------
# 城市 / 薪资 / 经验 / 学历
# ---------------------------------------------------------------------------


def detect_cities(text: str) -> list[str]:
    """Return major cities mentioned in ``text`` (ordered, deduplicated)."""
    if not text:
        return []
    found: list[tuple[int, str]] = []
    for city in MAJOR_CITIES:
        index = text.find(city)
        if index >= 0:
            found.append((index, city))
    found.sort(key=lambda item: (item[0], item[1]))
    result: list[str] = []
    for _, city in found:
        if city not in result:
            result.append(city)
    return result


@dataclass(frozen=True)
class SalaryRange:
    """A monthly salary range in CNY parsed from JD text."""

    min_monthly: int
    max_monthly: int
    raw: str
    months_per_year: int = 12

    @property
    def midpoint(self) -> float:
        return (self.min_monthly + self.max_monthly) / 2.0


_SALARY_K_RE = re.compile(
    r"(?P<a>\d{1,4})\s*[-~—–至到]\s*(?P<b>\d{1,4})\s*[kK千]\s*"
    r"(?:[xX*×·]\s*(?P<m>\d{1,2})\s*薪)?"
)
_SALARY_WAN_RE = re.compile(
    r"(?P<a>\d{1,3}(?:\.\d+)?)\s*[-~—–至到]\s*(?P<b>\d{1,3}(?:\.\d+)?)\s*万\s*"
    r"(?P<unit>[/／每]?\s*[月年]|薪)?"
)
_SALARY_YUAN_RE = re.compile(r"(?P<a>\d{4,6})\s*[-~—–至到]\s*(?P<b>\d{4,6})\s*元?")
_SALARY_SINGLE_K_RE = re.compile(r"(?P<a>\d{1,3})\s*[kK千]\s*(?:以上|\+)?")
_SALARY_MONTHS_RE = re.compile(r"(?P<m>\d{2})\s*薪")

_MIN_REASONABLE_MONTHLY = 800
_MAX_REASONABLE_MONTHLY = 500_000


def _clamp_salary(value: float) -> int | None:
    rounded = round(value)
    if _MIN_REASONABLE_MONTHLY <= rounded <= _MAX_REASONABLE_MONTHLY:
        return rounded
    return None


def parse_salary(text: str) -> SalaryRange | None:
    """Parse the first plausible monthly salary range from ``text``.

    Supports the common Chinese JD formats: ``25-40K·14薪``, ``2-3万/月``,
    ``20-35万/年``, ``15000-25000元`` and single-value ``15K以上`` forms.
    """
    if not text:
        return None
    months_match = _SALARY_MONTHS_RE.search(text)
    default_months = int(months_match.group("m")) if months_match else 12

    match = _SALARY_K_RE.search(text)
    if match:
        low = float(match.group("a")) * 1000
        high = float(match.group("b")) * 1000
        months = int(match.group("m")) if match.group("m") else default_months
        parsed = _build_salary(low, high, match.group(0), months)
        if parsed is not None:
            return parsed

    match = _SALARY_WAN_RE.search(text)
    if match:
        low_wan = float(match.group("a"))
        high_wan = float(match.group("b"))
        unit = (match.group("unit") or "").replace(" ", "")
        #"2-3万" 这类小额默认按月；“20-35万” 默认按年
        monthly_basis = unit in {"/月", "每月", "月", "薪"} or (unit == "" and high_wan <= 5)
        low = low_wan * (10000 if monthly_basis else 10000 / 12)
        high = high_wan * (10000 if monthly_basis else 10000 / 12)
        parsed = _build_salary(low, high, match.group(0).strip(), default_months)
        if parsed is not None:
            return parsed

    match = _SALARY_YUAN_RE.search(text)
    if match:
        low = float(match.group("a"))
        high = float(match.group("b"))
        parsed = _build_salary(low, high, match.group(0).strip(), default_months)
        if parsed is not None:
            return parsed

    match = _SALARY_SINGLE_K_RE.search(text)
    if match:
        value = float(match.group("a")) * 1000
        parsed = _build_salary(value, value, match.group(0).strip(), default_months)
        if parsed is not None:
            return parsed
    return None


def _build_salary(
    low: float, high: float, raw: str, months: int
) -> SalaryRange | None:
    if high < low:
        low, high = high, low
    low_monthly = _clamp_salary(low)
    high_monthly = _clamp_salary(high)
    if low_monthly is None or high_monthly is None:
        return None
    return SalaryRange(
        min_monthly=low_monthly,
        max_monthly=high_monthly,
        raw=raw,
        months_per_year=months if 12 <= months <= 20 else 12,
    )


_EXPERIENCE_RANGE_RE = re.compile(
    r"(?P<a>\d{1,2})\s*[-~—–至到]\s*(?P<b>\d{1,2})\s*年(?:以上)?(?:工作)?经验"
)
_EXPERIENCE_RANGE_PLAIN_RE = re.compile(r"(?P<a>\d{1,2})\s*[-~—–至到]\s*(?P<b>\d{1,2})\s*年")
_EXPERIENCE_MIN_RE = re.compile(r"(?P<a>\d{1,2})\s*年(?:及|或)?以上(?:工作)?经验?")
_EXPERIENCE_EXACT_RE = re.compile(r"(?P<a>\d{1,2})\s*年(?:工作)?经验")
_EXPERIENCE_FRESH_RE = re.compile(r"(应届|校招)")


def parse_experience(text: str) -> str | None:
    """Parse the experience requirement, e.g. ``3-5年`` / ``3年以上`` / ``不限``."""
    if not text:
        return None
    match = _EXPERIENCE_RANGE_RE.search(text) or _EXPERIENCE_RANGE_PLAIN_RE.search(text)
    if match:
        return f"{match.group('a')}-{match.group('b')}年"
    match = _EXPERIENCE_MIN_RE.search(text)
    if match:
        return f"{match.group('a')}年以上"
    match = _EXPERIENCE_EXACT_RE.search(text)
    if match:
        return f"{match.group('a')}年"
    if "经验不限" in text or "不限经验" in text or "工作年限不限" in text:
        return "不限"
    if _EXPERIENCE_FRESH_RE.search(text):
        return "应届"
    return None


def experience_bounds(value: str | None) -> tuple[float, float] | None:
    """Convert a parsed experience value into ``(min_years, max_years)``."""
    if not value:
        return None
    match = re.match(r"(\d{1,2})-(\d{1,2})年", value)
    if match:
        return float(match.group(1)), float(match.group(2))
    match = re.match(r"(\d{1,2})年以上", value)
    if match:
        return float(match.group(1)), 99.0
    match = re.match(r"(\d{1,2})年", value)
    if match:
        years = float(match.group(1))
        return years, years
    if value == "不限":
        return 0.0, 99.0
    if value == "应届":
        return 0.0, 1.0
    return None


def parse_education(text: str) -> str | None:
    """Parse the education requirement, e.g. ``本科及以上`` / ``硕士``."""
    if not text:
        return None
    if "学历不限" in text or "不限学历" in text:
        return "学历不限"
    best_level = -1
    best_keyword = ""
    for keyword, level in _DEGREE_LEVELS.items():
        index = text.find(keyword)
        if index >= 0 and level > best_level:
            best_level = level
            best_keyword = keyword
    if best_level < 0 or not best_keyword:
        return None
    suffix = "及以上" if f"{best_keyword}及以上" in text else ""
    return f"{best_keyword}{suffix}"


def degree_level(value: str | None) -> int | None:
    """Return the numeric degree level for a parsed education value."""
    if not value:
        return None
    if value == "学历不限" or value == "不限":
        return 0
    best: int | None = None
    for keyword, level in _DEGREE_LEVELS.items():
        if keyword in value and (best is None or level > best):
            best = level
    return best


_PERIOD_RE = re.compile(
    r"20\d{2}(?:\s*[./年\-]\s*\d{1,2}\s*月?)?\s*[-~—–至到]\s*"
    r"(?:20\d{2}(?:\s*[./年\-]\s*\d{1,2}\s*月?)?|至今|现在|present|now)",
    re.IGNORECASE,
)


def extract_periods(text: str) -> list[str]:
    """Return normalized date periods such as ``2021.07-2023.06`` found in text."""
    if not text:
        return []
    periods: list[str] = []
    for match in _PERIOD_RE.finditer(text):
        normalized = re.sub(r"\s+", "", match.group(0))
        normalized = normalized.replace("—", "-").replace("–", "-").replace("~", "-")
        #“至/到”仅在充当分隔符（后跟年份）时替换，否则会误伤“至今”
        normalized = re.sub(r"[至到](?=20\d{2})", "-", normalized)
        periods.append(normalized)
    return periods


def _period_boundaries(period: str) -> tuple[float, float] | None:
    """Return ``(start, end)`` year fractions for a period; ``end`` uses today."""
    parts = re.split(r"[-~—–]", period, maxsplit=1)
    if len(parts) != 2:
        return None
    start = _parse_year_fraction(parts[0])
    if start is None:
        return None
    end_token = parts[1].strip()
    if end_token in {"至今", "现在", "present", "now"}:
        from datetime import datetime

        today = datetime.now().astimezone().date()
        end = today.year + (today.month - 1) / 12.0
    else:
        end = _parse_year_fraction(end_token)
        if end is None:
            return None
    return start, end


def _parse_year_fraction(token: str) -> float | None:
    match = re.match(r"(20\d{2})(?:\D{0,2}(\d{1,2}))?", token.strip())
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    if not 1 <= month <= 12:
        month = 1
    return year + (month - 1) / 12.0


def guess_years_of_experience(periods: Iterable[str]) -> float | None:
    """Estimate total years of experience from a list of date periods."""
    spans: list[tuple[float, float]] = []
    for period in periods:
        boundaries = _period_boundaries(period)
        if boundaries is not None:
            spans.append(boundaries)
    if not spans:
        return None
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    return round(max(0.0, end - start), 1)


# ---------------------------------------------------------------------------
# 简历分节与联系方式
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "personal": ("个人信息", "基本信息", "联系方式", "个人资料"),
    "summary": ("自我评价", "自我介绍", "个人总结", "个人简介", "自我描述", "个人优势"),
    "education": ("教育经历", "教育背景", "学习经历", "教育信息", "学历背景"),
    "experience": ("工作经历", "工作经验", "职业经历", "实习经历", "工作履历", "实践经历"),
    "projects": ("项目经历", "项目经验", "项目介绍", "主要项目", "项目实践"),
    "skills": ("专业技能", "技能特长", "掌握技能", "技能清单", "技术栈", "技能", "IT技能"),
    "awards": ("荣誉奖项", "获奖经历", "奖项荣誉", "荣誉奖励", "荣誉"),
    "certificates": ("证书", "资格证书", "持证情况", "认证"),
}

_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•·▪◦]+\s*|[（(]?\d{1,2}[）).、]\s*(?!\d)|[✓√✅]\s*)")
_HEADER_DECOR_RE = re.compile(r"^[#*【\[]+\s*|\s*[#*】\]]+$")
_HEADER_INDEX_RE = re.compile(r"^[一二三四五六七八九十\d]{1,3}\s*[、.．)）]\s*")


def strip_bullet(line: str) -> str:
    """Remove list-marker decoration from a line."""
    return _BULLET_PREFIX_RE.sub("", line).strip()


def _section_header_of(line: str) -> tuple[str, str] | None:
    """Detect a section header; returns ``(section, inline_remainder)``."""
    cleaned = _HEADER_DECOR_RE.sub("", line.strip()).strip()
    if not cleaned:
        return None
    cleaned = _HEADER_INDEX_RE.sub("", cleaned).strip()
    remainder = ""
    for separator in ("：", ":"):
        if separator in cleaned:
            head, _, tail = cleaned.partition(separator)
            head, remainder = head.strip(), tail.strip()
            break
    else:
        head = cleaned
    if not head or len(head) > 14:
        return None
    for section, keywords in SECTION_KEYWORDS.items():
        if head in keywords:
            return section, remainder
    for section, keywords in SECTION_KEYWORDS.items():
        for keyword in keywords:
            if head.startswith(keyword) and len(head) <= len(keyword) + 6:
                return section, remainder
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """Split resume text into canonical sections keyed by ``SECTION_KEYWORDS``.

    Lines before the first detected header are collected under ``preamble``.
    """
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        header = _section_header_of(line)
        if header is not None:
            section, remainder = header
            current = section
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(strip_bullet(remainder))
            continue
        sections.setdefault(current, []).append(strip_bullet(line))
    return sections


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d(?:[\s-]?\d{4}){2}(?!\d)")
_URL_RE = re.compile(r"(?:https?://\S+|(?:github|gitee)\.com/[\w./-]+)")
_NAME_LABEL_RE = re.compile(r"姓\s*名\s*[:：]\s*([^\s,，|｜/／]{2,6})")
_LOCATION_LABEL_RE = re.compile(r"(?:现居|居住地|所在地|城市|所在城市)\s*[:：]?\s*([^\s,，|｜/／]{2,8})")


def extract_contact(text: str) -> dict[str, str]:
    """Extract name / phone / email / location / website from ``text``."""
    contact: dict[str, str] = {}
    email = _EMAIL_RE.search(text)
    if email:
        contact["email"] = email.group(0)
    phone = _PHONE_RE.search(text)
    if phone:
        contact["phone"] = re.sub(r"[\s-]", "", phone.group(0))
    url = _URL_RE.search(text)
    if url:
        contact["website"] = url.group(0).rstrip("。，,;；")

    name_match = _NAME_LABEL_RE.search(text)
    if name_match:
        contact["name"] = name_match.group(1)
    else:
        for line in text.splitlines()[:6]:
            cleaned = strip_bullet(line)
            if not cleaned or _section_header_of(line) is not None:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", cleaned) and not any(
                marker in cleaned for marker in ("简历", "个人", "求职")
            ):
                contact["name"] = cleaned
                break

    location_match = _LOCATION_LABEL_RE.search(text)
    if location_match:
        contact["location"] = location_match.group(1)
    else:
        cities = detect_cities("\n".join(text.splitlines()[:12]))
        if cities:
            contact["location"] = cities[0]
    return contact


_QUANTIFIED_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|％|倍|万\+?|亿|人|ms|毫秒|秒|分钟|小时|天|qps|tps|次|单|条|节点)"
    r"|(?:从|由)\s*\d[\d,.]*\s*(?:提升|增长|提高|降低|下降|减少|优化)\s*(?:到|至|为)\s*\d[\d,.]*",
    re.IGNORECASE,
)
#条目正文常见的动作动词前缀：用于避免把“2023.06 主导上线…”误判为经历条目起始
_ACTION_VERB_PREFIXES: tuple[str, ...] = (
    "主导",
    "负责",
    "参与",
    "完成",
    "搭建",
    "优化",
    "设计",
    "实现",
    "推动",
    "维护",
    "开发",
    "建设",
    "重构",
    "提升",
    "落地",
    "协助",
    "配合",
)

_TITLE_KEYWORDS: tuple[str, ...] = (
    "开发工程师",
    "工程师",
    "开发",
    "架构师",
    "技术专家",
    "专家",
    "经理",
    "主管",
    "总监",
    "分析师",
    "专员",
    "设计师",
    "负责人",
    "测试",
    "运维",
    "算法",
    "产品",
    "运营",
    "研究员",
    "实习生",
    "实习",
    "开发实习",
    "CTO",
    "CEO",
)
_COMPANY_MARKERS: tuple[str, ...] = (
    "公司",
    "集团",
    "科技",
    "网络",
    "信息",
    "软件",
    "技术",
    "银行",
    "证券",
    "保险",
    "研究院",
    "研究所",
    "实验室",
    "有限",
    "传媒",
    "文化",
    "教育",
    "医疗",
    "智能",
    "数据",
    "电子商务",
    "工作室",
)


def count_quantified_lines(lines: Iterable[str]) -> int:
    """Return how many lines contain quantified achievement tokens."""
    count = 0
    for line in lines:
        if _QUANTIFIED_RE.search(line):
            count += 1
    return count


def count_quantified_tokens(text: str) -> int:
    """Return the total number of quantified tokens found in ``text``."""
    if not text:
        return 0
    return len(_QUANTIFIED_RE.findall(text))


def _has_title_keyword(line: str) -> bool:
    return any(keyword in line for keyword in _TITLE_KEYWORDS)


def _has_company_marker(line: str) -> bool:
    return any(marker in line for marker in _COMPANY_MARKERS)


# ---------------------------------------------------------------------------
# 结构化简历
# ---------------------------------------------------------------------------


@dataclass
class ParsedResume:
    """Structured resume data extracted from free-form text."""

    personal_info: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    technical_skills: list[str] = field(default_factory=list)
    soft_skills: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    experience: list[dict[str, Any]] = field(default_factory=list)
    education: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    awards: list[str] = field(default_factory=list)
    years_of_experience: float | None = None
    ats_score: float = 0.0

    @property
    def all_skills(self) -> list[str]:
        merged = list(self.technical_skills)
        for skill in self.soft_skills:
            if skill not in merged:
                merged.append(skill)
        return merged

    def to_dict(self) -> dict[str, Any]:
        """Return the plan-specified structured resume mapping."""
        return {
            "personal_info": dict(self.personal_info),
            "summary": self.summary,
            "skills": {
                "technical": list(self.technical_skills),
                "soft": list(self.soft_skills),
                "languages": list(self.languages),
            },
            "experience": [dict(item) for item in self.experience],
            "education": [dict(item) for item in self.education],
            "projects": [dict(item) for item in self.projects],
            "awards": list(self.awards),
            "years_of_experience": self.years_of_experience,
            "ats_score": self.ats_score,
        }


def _remove_periods(line: str) -> str:
    return _PERIOD_RE.sub(" ", line).strip()


def _split_entry_parts(line: str) -> list[str]:
    cleaned = _remove_periods(line)
    parts = re.split(r"[|｜/／·•,，;；\s]+", cleaned)
    return [part for part in parts if part]


def _extract_company_title(line: str) -> tuple[str, str]:
    """Best-effort split of an entry header into ``(company, title)``."""
    parts = _split_entry_parts(line)
    title = ""
    company = ""
    for part in parts:
        if not title and _has_title_keyword(part):
            title = part
    for part in parts:
        if part == title:
            continue
        if _has_company_marker(part):
            company = part
            break
    if not company and parts:
        company = parts[0] if parts[0] != title else (parts[1] if len(parts) > 1 else "")
    return company, title


def _parse_dated_entries(
    lines: list[str],
    *,
    key_name: str,
    require_project_marker: bool = False,
) -> list[dict[str, Any]]:
    """Group section lines into dated entries (experience / project blocks)."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending: list[str] = []

    def _flush() -> None:
        nonlocal current, pending
        if current is not None:
            entries.append(current)
        elif pending:
            fallback: dict[str, Any] = {
                key_name: pending[0][:60],
                "period": "",
                "highlights": pending[1:],
            }
            fallback.setdefault("title", "")
            entries.append(fallback)
        current = None
        pending = []

    for line in lines:
        periods = extract_periods(line)
        is_bullet = bool(_BULLET_PREFIX_RE.match(line))
        starts_entry = False
        if periods:
            starts_entry = (
                _has_title_keyword(line)
                or _has_company_marker(line)
                or (require_project_marker and "项目" in line)
                or (
                    not is_bullet
                    and len(_remove_periods(line)) <= 24
                    and not any(
                        _remove_periods(line).startswith(verb)
                        for verb in _ACTION_VERB_PREFIXES
                    )
                )
            )
        elif require_project_marker and "项目" in line and len(line) <= 40 and not is_bullet:
            starts_entry = True
        if starts_entry:
            _flush()
            company, title = _extract_company_title(line)
            name = _remove_periods(line)[:80]
            if require_project_marker:
                current = {
                    key_name: name,
                    "period": periods[0] if periods else "",
                    "highlights": [],
                }
            else:
                current = {
                    "company": company,
                    "title": title,
                    "period": periods[0] if periods else "",
                    "highlights": [],
                }
            continue
        if current is not None:
            if line:
                current["highlights"].append(line)
        elif line:
            pending.append(line)
    _flush()
    return [entry for entry in entries if entry.get("highlights") or entry.get(key_name)]


def _extract_major(line: str) -> str:
    """Best-effort extraction of an education entry's major."""
    labeled = re.search(r"专业\s*[:：]?\s*([\u4e00-\u9fffA-Za-z ]{2,18})", line)
    if labeled:
        return labeled.group(1).strip()
    suffixed = re.search(r"([\u4e00-\u9fff]{2,12})专业", line)
    if suffixed:
        return suffixed.group(1).strip()
    #“计算机科学与技术 本科”这类无“专业”二字的形式：取学位词前紧邻的中文短语
    before_degree = re.search(
        r"([\u4e00-\u9fff]{2,14})\s*(?:专业)?\s*(?:本科|硕士|学士|大专|专科|研究生|博士)",
        line,
    )
    if before_degree:
        candidate = before_degree.group(1)
        if "大学" not in candidate and "学院" not in candidate and "学校" not in candidate:
            return candidate
    return ""


def _parse_education_entries(lines: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        school = ""
        school_match = re.search(r"[\u4e00-\u9fff]{2,16}(?:大学|学院|学校)", line)
        if school_match:
            school = school_match.group(0)
        degree = parse_education(line)
        major = _extract_major(line)
        periods = extract_periods(line)
        if school:
            current = {
                "school": school,
                "degree": degree or "",
                "major": major,
                "period": periods[0] if periods else "",
            }
            entries.append(current)
            continue
        if current is not None:
            if degree and not current["degree"]:
                current["degree"] = degree
            if major and not current["major"]:
                current["major"] = major
            if periods and not current["period"]:
                current["period"] = periods[0]
        elif degree and periods:
            entries.append(
                {
                    "school": "",
                    "degree": degree,
                    "major": major,
                    "period": periods[0],
                }
            )
            current = entries[-1]
    return entries


def parse_resume_text(text: str) -> ParsedResume:
    """Parse free-form resume text into a :class:`ParsedResume`."""
    sections = split_sections(text)
    resume = ParsedResume()
    resume.personal_info = extract_contact(text)

    summary_lines = sections.get("summary", [])
    resume.summary = "\n".join(summary_lines)[:600]

    skills_lines = sections.get("skills", [])
    if skills_lines:
        declared = extract_skills("\n".join(skills_lines))
    else:
        declared = []
    #在技能节之外，经历/项目描述中体现的技术能力同样计入技能清单
    evidenced = [skill for skill in extract_skills(text) if skill not in _SOFT_SKILL_SET]
    merged = list(declared)
    for skill in evidenced:
        if skill not in merged:
            merged.append(skill)
    resume.technical_skills = merged
    resume.soft_skills = [skill for skill in extract_soft_skills(text) if skill not in resume.technical_skills]
    resume.languages = extract_languages(text)

    resume.experience = _parse_dated_entries(sections.get("experience", []), key_name="company")
    resume.education = _parse_education_entries(sections.get("education", []))
    resume.projects = _parse_dated_entries(
        sections.get("projects", []), key_name="name", require_project_marker=True
    )
    resume.awards = sections.get("awards", [])[:10]

    periods = extract_periods("\n".join(sections.get("experience", [])))
    if not periods:
        periods = extract_periods(text)
    resume.years_of_experience = guess_years_of_experience(periods)
    return resume


# ---------------------------------------------------------------------------
# 结构化 JD
# ---------------------------------------------------------------------------

JD_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "responsibilities": ("岗位职责", "工作职责", "工作内容", "职责描述", "职位描述", "你将负责", "主要职责"),
    "requirements": ("任职要求", "任职资格", "岗位要求", "职位要求", "任职条件", "技能要求", "我们希望你", "岗位条件"),
    "benefits": ("福利待遇", "薪资福利", "员工福利", "薪酬待遇", "我们提供", "福利"),
}

_PREFERRED_MARKERS: tuple[str, ...] = ("加分", "优先", "nice to have", "bonus", "锦上添花")

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("后端", ("后端", "服务端", "微服务", "django", "flask", "fastapi", "spring", "gin", "grpc", "分布式", "高并发", "restful")),
    ("前端", ("前端", "react", "vue", "angular", "webpack", "vite", "javascript", "typescript", "小程序", "h5", "css", "html")),
    ("移动端", ("android", "ios", "客户端", "flutter", "react native", "objective-c", "swift")),
    ("算法", ("算法工程师", "机器学习", "深度学习", "推荐", "搜索", "nlp", "自然语言", "计算机视觉", "大模型", "llm", "aigc")),
    ("数据", ("数据分析", "数据开发", "数仓", "大数据", "etl", "spark", "hive", "flink", "数据挖掘", "bi")),
    ("测试", ("测试", "qa", "自动化测试", "性能测试", "测试开发")),
    ("运维", ("运维", "sre", "devops", "kubernetes", "k8s", "云原生", "系统工程师")),
    ("安全", ("安全工程师", "渗透", "漏洞", "风控", "网络安全")),
    ("产品", ("产品经理", "产品设计", "需求分析", "产品规划", "原型")),
    ("运营", ("运营", "用户增长", "活动策划", "内容运营", "新媒体")),
    ("设计", ("设计师", "ui设计", "ux", "交互设计", "视觉设计")),
)


def classify_direction(text: str) -> str | None:
    """Classify a JD into a role direction (前端/后端/算法/...)."""
    if not text:
        return None
    lowered = text.lower()
    best: tuple[int, str] | None = None
    for category, keywords in CATEGORY_KEYWORDS:
        score = sum(lowered.count(keyword.lower()) for keyword in keywords)
        if score > 0 and (best is None or score > best[0]):
            best = (score, category)
    return best[1] if best is not None else None


@dataclass
class ParsedJD:
    """Structured job-description data extracted from free-form text."""

    title: str = ""
    company: str = ""
    city: str = ""
    salary: SalaryRange | None = None
    experience: str = ""
    education: str = ""
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    category: str | None = None
    keywords: list[str] = field(default_factory=list)
    url: str = ""
    posted_date: str = ""
    raw_text: str = ""

    @property
    def all_skills(self) -> list[str]:
        merged = list(self.required_skills)
        for skill in self.preferred_skills:
            if skill not in merged:
                merged.append(skill)
        return merged

    @property
    def completeness(self) -> float:
        """Field-extraction completeness in [0, 1] (8 key fields)."""
        checks = (
            bool(self.title),
            bool(self.company),
            bool(self.city),
            self.salary is not None,
            bool(self.experience),
            bool(self.education),
            bool(self.required_skills),
            bool(self.responsibilities),
        )
        return sum(1 for item in checks if item) / len(checks)

    def to_dict(self) -> dict[str, Any]:
        salary = None
        if self.salary is not None:
            salary = {
                "min_monthly": self.salary.min_monthly,
                "max_monthly": self.salary.max_monthly,
                "raw": self.salary.raw,
                "months_per_year": self.salary.months_per_year,
            }
        return {
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "salary": salary,
            "experience": self.experience,
            "education": self.education,
            "required_skills": list(self.required_skills),
            "preferred_skills": list(self.preferred_skills),
            "responsibilities": list(self.responsibilities),
            "benefits": list(self.benefits),
            "category": self.category,
            "keywords": list(self.keywords),
            "url": self.url,
            "posted_date": self.posted_date,
            "completeness": round(self.completeness, 2),
        }


def _jd_section_header_of(line: str) -> str | None:
    cleaned = _HEADER_DECOR_RE.sub("", line.strip()).strip()
    if not cleaned:
        return None
    cleaned = _HEADER_INDEX_RE.sub("", cleaned).strip()
    for separator in ("：", ":"):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[0].strip()
            break
    if not cleaned or len(cleaned) > 14:
        return None
    for section, keywords in JD_SECTION_KEYWORDS.items():
        if cleaned in keywords:
            return section
    for section, keywords in JD_SECTION_KEYWORDS.items():
        for keyword in keywords:
            if cleaned.startswith(keyword) and len(cleaned) <= len(keyword) + 6:
                return section
    return None


def _extract_jd_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        header = _jd_section_header_of(line)
        if header is not None:
            current = header
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(strip_bullet(line))
    return sections


def _extract_jd_title(text: str) -> str:
    label = re.search(r"(?:岗位|职位|职务)(?:名称)?\s*[:：]\s*([^\n|｜,，]{2,30})", text)
    if label:
        return label.group(1).strip()
    for line in text.splitlines()[:15]:
        cleaned = strip_bullet(line).strip()
        if not cleaned or len(cleaned) > 30:
            continue
        if any(marker in cleaned for marker in ("职责", "要求", "负责", "描述")):
            continue
        if _has_title_keyword(cleaned):
            return cleaned
    return ""


def _extract_jd_company(text: str) -> str:
    label = re.search(r"公司(?:名称)?\s*[:：]\s*([^\n|｜,，]{2,30})", text)
    if label:
        return label.group(1).strip()
    for line in text.splitlines()[:8]:
        cleaned = strip_bullet(line).strip()
        if not cleaned or len(cleaned) > 30:
            continue
        match = re.search(r"[\u4e00-\u9fffA-Za-z0-9]{2,20}(?:有限公司|集团|科技|研究院)", cleaned)
        if match:
            return match.group(0)
    return ""


def parse_jd_text(
    text: str,
    *,
    title_hint: str = "",
    company_hint: str = "",
    city_hint: str = "",
    url: str = "",
    posted_date: str = "",
) -> ParsedJD:
    """Parse free-form JD text into a :class:`ParsedJD`.

    ``*_hint`` values win over text-derived values so callers (e.g. the web
    scraper in Phase 4) can pass trusted metadata.
    """
    jd = ParsedJD(raw_text=text, url=url, posted_date=posted_date)
    if not text.strip():
        return jd
    sections = _extract_jd_sections(text)

    jd.title = title_hint.strip() or _extract_jd_title(text)
    jd.company = company_hint.strip() or _extract_jd_company(text)

    cities = detect_cities("\n".join(text.splitlines()[:10]))
    if not cities:
        cities = detect_cities(text)
    jd.city = city_hint.strip() or (cities[0] if cities else "")

    jd.salary = parse_salary(text)

    requirement_text = "\n".join(sections.get("requirements", []))
    jd.experience = parse_experience(requirement_text) or parse_experience(text) or ""
    jd.education = parse_education(requirement_text) or parse_education(text) or ""

    #优先技能 = 加分/优先行的技能；必备技能 = 全文技能 - 优先技能
    preferred_lines = [
        line
        for line in text.splitlines()
        if any(marker in line.lower() for marker in _PREFERRED_MARKERS)
    ]
    preferred_set = set(extract_skills("\n".join(preferred_lines)))
    all_skills = extract_skills(text)
    jd.preferred_skills = [skill for skill in all_skills if skill in preferred_set]
    jd.required_skills = [skill for skill in all_skills if skill not in preferred_set]

    jd.responsibilities = sections.get("responsibilities", [])[:12]
    jd.benefits = sections.get("benefits", [])[:10]
    jd.category = classify_direction(text)
    jd.keywords = jd.required_skills[:12]
    return jd
