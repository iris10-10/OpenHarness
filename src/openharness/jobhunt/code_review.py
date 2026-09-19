"""Static heuristic checks over submitted algorithm solutions.

The ``algorithm_review`` tool reports findings from :func:`review_code`;
every check is a deterministic text/indentation heuristic (the tool output
labels them as such) — the submitted code is never executed:

- loop-nesting depth → complexity risk (O(n²)+)
- sorting inside a loop → an extra O(n log n) factor per iteration
- list membership tests inside loops → convert the list to a set
- string concatenation with ``+=`` in loops → ``"".join(...)``
- recursion without memoization → ``lru_cache`` / bottom-up DP
- mutable default arguments (classic Python bug)
- language idioms: Java string ``==``, JS loose equality,
  ``range(len(...))``, C++ ``std::endl``

:func:`review_code` returns findings plus a summary (detected language,
max loop nesting, coarse complexity estimate).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["EDGE_CASE_CHECKLIST", "CodeFinding", "detect_language", "review_code"]

EDGE_CASE_CHECKLIST: tuple[str, ...] = (
    "空输入 / None：函数是否直接崩溃",
    "单元素与两元素：循环与滑窗是否越界",
    "全部相同与包含重复：去重逻辑是否多算漏算",
    "边界下标：首尾元素是否被访问到",
    "极值：溢出（非 Python 语言）与负数取模",
    "递归深度：最坏输入会不会爆栈",
)

_LANGUAGE_HINTS: dict[str, tuple[re.Pattern[str], ...]] = {
    "python": (
        re.compile(r"^\s*def\s+\w+\s*\(", re.MULTILINE),
        re.compile(r"^\s*(?:class|import|from)\s+\w+", re.MULTILINE),
        re.compile(r"\bself\b"),
        re.compile(r":\s*$", re.MULTILINE),
    ),
    "java": (
        re.compile(r"\bpublic\s+(?:static\s+)?(?:class|void|int|boolean|String)\b"),
        re.compile(r"\bSystem\.out\b"),
        re.compile(r"\bprivate\s+\w+"),
        re.compile(r"\b(?:int\[\]|List<|Map<|new\s+\w+\[)"),
    ),
    "javascript": (
        re.compile(r"\b(?:const|let)\s+\w+\s*="),
        re.compile(r"=>"),
        re.compile(r"\bfunction\s+\w+\s*\("),
        re.compile(r"\bconsole\.log\b"),
    ),
    "cpp": (
        re.compile(r"#include\s*<"),
        re.compile(r"\bstd::"),
        re.compile(r"\bvector\s*<"),
        re.compile(r"\busing\s+namespace\s+std\b"),
    ),
    "go": (
        re.compile(r"^\s*package\s+\w+", re.MULTILINE),
        re.compile(r"\bfunc\s+\w+\s*\("),
        re.compile(r":="),
    ),
}

_LOOP_START = re.compile(r"^\s*(?:for|while)\b")
_LOOP_HEADER_BRACE = re.compile(r"\b(?:for|while)\s*\(([^()]|\([^()]*\))*\)\s*\{")
_SORT_CALL = re.compile(r"\.sort\(|sorted\(")
_LIST_LITERAL = re.compile(r"\b(\w+)\s*=\s*\[")
_STRING_LITERAL = re.compile(r"\b(\w+)\s*=\s*(['\"])\2")
_MEMBERSHIP = r"\bin\s+{name}\b"
_CONCAT = r"\b{name}\s*\+="
_FUNC_DEF = re.compile(r"^\s*def\s+(\w+)\s*\(", re.MULTILINE)
_MUTABLE_DEFAULT = re.compile(r"def\s+\w+\s*\([^)]*=\s*[\[{]")
_RANGE_LEN = re.compile(r"range\s*\(\s*len\s*\(")
_WHILE_TRUE = re.compile(r"\bwhile\s+True\s*:")
_JAVA_STRING_EQ = re.compile(r'"[^"\n]*"\s*==|==\s*"[^"\n]*"')
_JS_LOOSE_EQ = re.compile(r"(?<![=!<>])==(?!=)|(?<![=!<>])!=(?!=)")
_CPP_ENDL = re.compile(r"\bstd::endl\b")
_CPP_SIZE_IN_FOR = re.compile(r"for\s*\([^)]*\.size\s*\(\s*\)")
_PY_MID_HINT = re.compile(r"//\s*2|>>\s*1")
_MEMO_HINTS = ("lru_cache", "@cache", "memo", "dp[", "dp =")

_BRACE_LANGUAGES = {"java", "javascript", "cpp", "go"}


@dataclass(frozen=True)
class CodeFinding:
    """One heuristic finding with a concrete fix."""

    severity: str
    category: str
    message: str
    suggestion: str
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "suggestion": self.suggestion,
        }
        if self.line:
            payload["line"] = self.line
        return payload


def detect_language(code: str) -> str:
    """Best-effort language detection; '' when no hint matches."""
    best = ""
    best_score = 0
    for language, patterns in _LANGUAGE_HINTS.items():
        score = sum(1 for pattern in patterns if pattern.search(code))
        if score > best_score:
            best = language
            best_score = score
    return best


def _python_loop_depths(code: str) -> tuple[int, dict[int, int]]:
    """Max loop nesting + per-line loop nesting via indentation."""
    stack: list[int] = []
    max_depth = 0
    depths: dict[int, int] = {}
    for number, line in enumerate(code.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        while stack and indent <= stack[-1]:
            stack.pop()
        if _LOOP_START.match(line):
            stack.append(indent)
        depths[number] = len(stack)
        max_depth = max(max_depth, len(stack))
    return max_depth, depths


def _brace_loop_depths(code: str) -> tuple[int, dict[int, int]]:
    """Max loop nesting + per-loop depth via brace tracking."""
    starts = {match.end() - 1 for match in _LOOP_HEADER_BRACE.finditer(code)}
    stack: list[bool] = []
    max_depth = 0
    depths: dict[int, int] = {}
    line = 1
    for index, char in enumerate(code):
        if char == "\n":
            line += 1
        elif char == "{":
            is_loop = index in starts
            stack.append(is_loop)
            if is_loop:
                depth = sum(1 for flag in stack if flag)
                depths[line] = depth
                max_depth = max(max_depth, depth)
        elif char == "}" and stack:
            stack.pop()
    return max_depth, depths


def _loop_depths(code: str, language: str) -> tuple[int, dict[int, int]]:
    if language == "python":
        return _python_loop_depths(code)
    if language in _BRACE_LANGUAGES:
        return _brace_loop_depths(code)
    indent_depth, indent_map = _python_loop_depths(code)
    brace_depth, brace_map = _brace_loop_depths(code)
    if brace_depth > indent_depth:
        return brace_depth, brace_map
    return indent_depth, indent_map


def _complexity_estimate(max_depth: int, *, sorting: bool, binary: bool) -> str:
    if binary and max_depth <= 1:
        base = "O(log n) 量级（检测到二分模式）"
    elif max_depth <= 0:
        base = "O(1) ~ O(n)（未检测到循环嵌套）"
    elif max_depth == 1:
        base = "O(n) 量级（单层循环）"
    elif max_depth == 2:
        base = "O(n²) 量级（双层循环）"
    else:
        base = f"O(n^{max_depth}) 量级（{max_depth} 层循环嵌套）"
    if sorting and max_depth >= 1:
        base += "；含排序时额外 × O(n log n) 因子"
    return base


def _sorted_in_loop(depths: dict[int, int], lines: list[str]) -> int:
    for number in sorted(depths):
        if depths[number] < 1:
            continue
        content = lines[number - 1] if number <= len(lines) else ""
        if _SORT_CALL.search(content):
            return number
    return 0


def review_code(code: str, *, language: str = "") -> tuple[list[CodeFinding], dict[str, Any]]:
    """Run the heuristic checks; returns (findings, summary)."""
    detected = language.strip().lower() or detect_language(code)
    lines = code.splitlines()
    max_depth, depths = _loop_depths(code, detected)
    findings: list[CodeFinding] = []

    # 1) 循环嵌套 -> 复杂度风险
    if max_depth >= 2:
        deepest_line = min(
            number for number in depths if depths[number] == max_depth
        )
        estimate = _complexity_estimate(
            max_depth,
            sorting=bool(_SORT_CALL.search(code)),
            binary=False,
        )
        findings.append(
            CodeFinding(
                "警告",
                "复杂度",
                f"检测到 {max_depth} 层循环嵌套，最坏时间复杂度可能达到 {estimate}",
                "尝试用哈希表/双指针/前缀和/单调栈去掉内层循环；数据有序时可考虑二分。",
                line=deepest_line,
            )
        )

    # 2) 循环内排序
    sort_line = _sorted_in_loop(depths, lines) if detected in ("python", "") else 0
    if sort_line:
        findings.append(
            CodeFinding(
                "提示",
                "复杂度",
                f"第 {sort_line} 行在循环内排序：整体复杂度会被放大 O(n log n) 倍",
                "预先排序一次，或用堆维护局部有序（如 Top-K 小顶堆）。",
                line=sort_line,
            )
        )

    # 3) 循环内列表成员检查（Python）
    if detected in ("python", ""):
        list_vars = {match.group(1) for match in _LIST_LITERAL.finditer(code)}
        for number in sorted(depths):
            if depths[number] < 1 or number > len(lines):
                continue
            content = lines[number - 1]
            hit = next(
                (
                    variable
                    for variable in list_vars
                    if re.search(_MEMBERSHIP.format(name=re.escape(variable)), content)
                ),
                "",
            )
            if hit:
                findings.append(
                    CodeFinding(
                        "建议",
                        "性能",
                        f"第 {number} 行在循环内用 `in` 检查列表（每次 O(n)）",
                        f"把 '{hit}' 构建为 set 后再判断，成员检查降为 O(1)。",
                        line=number,
                    )
                )
                break

    # 4) 循环内字符串拼接（Python）
    if detected in ("python", ""):
        string_vars = {match.group(1) for match in _STRING_LITERAL.finditer(code)}
        for number in sorted(depths):
            if depths[number] < 1 or number > len(lines):
                continue
            content = lines[number - 1]
            hit = next(
                (
                    variable
                    for variable in string_vars
                    if re.search(_CONCAT.format(name=re.escape(variable)), content)
                ),
                "",
            )
            if hit:
                findings.append(
                    CodeFinding(
                        "建议",
                        "性能",
                        f"第 {number} 行在循环内用 '+=' 拼接字符串：Python 字符串不可变，会平方级退化",
                        "用列表收集片段，循环结束后 \"\".join(...) 拼接。",
                        line=number,
                    )
                )
                break

    # 5) 递归未见记忆化（Python）
    if detected in ("python", ""):
        functions = set(_FUNC_DEF.findall(code))
        recursive = [
            name
            for name in functions
            if len(re.findall(rf"\b{re.escape(name)}\s*\(", code)) >= 2
        ]
        if recursive and not any(hint in code for hint in _MEMO_HINTS):
            findings.append(
                CodeFinding(
                    "提示",
                    "复杂度",
                    f"疑似递归实现（{'、'.join(sorted(recursive))} 调用了自身）且未见记忆化",
                    "若存在重叠子问题，加 @lru_cache 或改写为自底向上 DP，避免指数级退化。",
                )
            )

    # 6) 可变默认参数（Python）
    if detected in ("python", "") and _MUTABLE_DEFAULT.search(code):
        findings.append(
            CodeFinding(
                "隐患",
                "正确性",
                "函数默认参数是可变对象（[] 或 {}）：默认值只在定义时求值，多次调用会共享状态",
                "改用 None 作默认值，在函数体内按需初始化。",
            )
        )

    # 7) range(len(...))（Python）
    if detected in ("python", "") and _RANGE_LEN.search(code):
        findings.append(
            CodeFinding(
                "提示",
                "可读性",
                "出现 range(len(x)) 写法，下标操作容易出错",
                "优先用 enumerate(...) 或 zip(...)；确实需要下标再退回去。",
            )
        )

    # 8) while True 无 break
    if _WHILE_TRUE.search(code) and "break" not in code:
        findings.append(
            CodeFinding(
                "隐患",
                "正确性",
                "检测到 while True 且代码中没有 break",
                "确认循环有其它退出路径（return / 抛异常），否则可能死循环。",
            )
        )

    # 9) Java 字符串 == 比较
    if detected == "java" and _JAVA_STRING_EQ.search(code):
        findings.append(
            CodeFinding(
                "警告",
                "正确性",
                "字符串与 == 比较：比较的是引用而非内容",
                "改用 equals()；把常量放前面（\"x\".equals(s)）可顺手防 NPE。",
            )
        )

    # 10) JS 宽松相等
    if detected == "javascript" and _JS_LOOSE_EQ.search(code):
        findings.append(
            CodeFinding(
                "提示",
                "正确性",
                "使用了 == / != 宽松相等，存在隐式类型转换风险",
                "统一改用 === / !==。",
            )
        )

    # 11) C++ std::endl
    if detected == "cpp":
        if _CPP_ENDL.search(code):
            findings.append(
                CodeFinding(
                    "提示",
                    "性能",
                    "std::endl 每次都会 flush 缓冲",
                    "大量输出时改用 '\\n'，避免频繁刷新。",
                )
            )
        if _CPP_SIZE_IN_FOR.search(code):
            findings.append(
                CodeFinding(
                    "提示",
                    "性能",
                    "for 条件里的 .size() 每次迭代都会求值",
                    "提前用变量缓存 size（注意循环中容器变化时不要缓存）。",
                )
            )

    findings.sort(key=lambda finding: (finding.line, finding.category))
    binary = bool(_PY_MID_HINT.search(code)) and bool(re.search(r"\bwhile\b", code))
    summary: dict[str, Any] = {
        "language": detected or "unknown",
        "line_count": len(lines),
        "max_loop_depth": max_depth,
        "complexity_estimate": _complexity_estimate(
            max_depth,
            sorting=bool(_SORT_CALL.search(code)),
            binary=binary,
        ),
        "finding_count": len(findings),
    }
    return findings, summary
