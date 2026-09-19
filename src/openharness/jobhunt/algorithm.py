"""Deterministic algorithm-practice engine for the Phase 2 algorithm tools.

Two pieces live here, both offline and reproducible:

- a curated bank of classic interview problems (arrays → graphs → DP) with
  topic / difficulty / one-line approach hint, used by
  ``algorithm_recommend`` as the fallback when the RAG interview collection
  holds no company-specific problems;
- :func:`analyze_problem`, a keyword-driven classifier mapping a problem
  statement onto its topic framework (解题步骤 / 常见陷阱 / 复杂度目标 /
  面试追问), so 思路讲解 stays grounded in checklists instead of invented code.

Company-specific weighting only happens when real problems come back from
the RAG store in the tool layer; nothing here fabricates company statistics.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ALGORITHM_DIFFICULTIES",
    "TOPIC_LABELS",
    "AlgorithmProblem",
    "ProblemAnalysis",
    "analyze_problem",
    "detect_topics",
    "problems_for_topic",
    "resolve_topic",
    "select_problems",
]

#刷题难度档位（"混合" 表示不筛选）
ALGORITHM_DIFFICULTIES: tuple[str, ...] = ("简单", "中等", "困难")

_DIFFICULTY_ORDER: dict[str, int] = {"简单": 0, "中等": 1, "困难": 2}

#主题键（英文，与题库/框架字段一致）-> 中文展示名
TOPIC_LABELS: dict[str, str] = {
    "array": "数组与哈希",
    "two_pointers": "双指针",
    "sliding_window": "滑动窗口",
    "linked_list": "链表",
    "stack_queue": "栈与队列",
    "binary_tree": "二叉树",
    "heap": "堆（优先队列）",
    "graph": "图与搜索",
    "binary_search": "二分查找",
    "dp": "动态规划",
    "greedy": "贪心",
    "backtracking": "回溯",
    "string": "字符串",
    "bit": "位运算",
    "prefix_sum": "前缀和",
}


@dataclass(frozen=True)
class AlgorithmProblem:
    """One classic practice problem with its topic and approach hint."""

    problem: str
    number: int
    topic: str
    difficulty: str
    hint: str

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "problem": self.problem,
            "topic": self.topic,
            "topic_label": TOPIC_LABELS.get(self.topic, self.topic),
            "difficulty": self.difficulty,
            "hint": self.hint,
        }
        if self.number:
            payload["leetcode"] = self.number
        return payload


# ---------------------------------------------------------------------------
# 经典题库：(题目, LeetCode 题号（0 表示无题号）, 主题, 难度, 一句话思路)
# ---------------------------------------------------------------------------

_PROBLEM_ROWS: tuple[tuple[str, int, str, str, str], ...] = (
    # ------------------------------------------------------- 数组与哈希
    ("两数之和", 1, "array", "简单", "哈希表一次遍历：先查 target-num 是否已见，再存入当前值"),
    ("三数之和", 15, "array", "中等", "排序后固定一个数，其余用对撞双指针；注意三层去重"),
    ("合并区间", 56, "array", "中等", "按左端点排序后线性扫描，能合并就更新右端点"),
    ("轮转数组", 189, "array", "中等", "三次反转：整体反转后两段分别反转"),
    ("除自身以外数组的乘积", 238, "array", "中等", "前缀积与后缀积两趟扫描，避免使用除法"),
    ("最大子数组和", 53, "array", "中等", "Kadane：f(i) = max(nums[i], f(i-1) + nums[i])"),
    ("移动零", 283, "array", "简单", "快慢指针：慢指针指向下一个待填位置，快指针找非零值"),
    ("缺失的第一个正数", 41, "array", "困难", "原地哈希：把数值 x 交换到下标 x-1 处，再扫描第一处不匹配"),
    # ------------------------------------------------------------- 双指针
    ("盛最多水的容器", 11, "two_pointers", "中等", "对撞双指针，每次移动较短的一侧并更新最大面积"),
    ("合并两个有序数组", 88, "two_pointers", "简单", "三指针从尾部归并，避免覆盖未处理数据"),
    ("颜色分类", 75, "two_pointers", "中等", "荷兰国旗三指针分区：0 区右扩、2 区左扩"),
    ("验证回文串", 125, "two_pointers", "简单", "对撞双指针，跳过非字母数字字符后比较"),
    ("两数之和 II - 输入有序数组", 167, "two_pointers", "中等", "利用有序性：和偏小移左指针、偏大移右指针"),
    # ----------------------------------------------------------- 滑动窗口
    ("无重复字符的最长子串", 3, "sliding_window", "中等", "滑动窗口 + 字符计数，遇到重复收缩左边界"),
    ("最小覆盖子串", 76, "sliding_window", "困难", "滑动窗口 + need/have 计数，命中全部需求时收缩并记录最短"),
    ("找到字符串中所有字母异位词", 438, "sliding_window", "中等", "固定长度窗口计数比对"),
    ("长度最小的子数组", 209, "sliding_window", "中等", "正数数组上的可变窗口，和达标即收缩"),
    ("滑动窗口最大值", 239, "sliding_window", "困难", "单调递减双端队列维护窗口最大值"),
    # --------------------------------------------------------------- 链表
    ("反转链表", 206, "linked_list", "简单", "迭代三指针（prev/curr/next）或递归"),
    ("环形链表", 141, "linked_list", "简单", "快慢指针，相遇即有环"),
    ("环形链表 II", 142, "linked_list", "中等", "相遇后一指针回到头部同速前进，再次相遇处为环入口"),
    ("合并两个有序链表", 21, "linked_list", "简单", "哨兵节点 + 双指针逐个拼接"),
    ("两两交换链表中的节点", 24, "linked_list", "中等", "迭代（三指针）或递归交换相邻节点"),
    ("K 个一组翻转链表", 25, "linked_list", "困难", "先统计长度按组截断，组内反转后与前后衔接"),
    ("相交链表", 160, "linked_list", "简单", "双指针走完各自路径后切换到对方头部"),
    ("排序链表", 148, "linked_list", "中等", "快慢指针找中点 + 归并排序"),
    ("LRU 缓存", 146, "linked_list", "中等", "哈希表 + 双向链表实现 O(1) 查询与淘汰"),
    # ----------------------------------------------------------- 栈与队列
    ("有效的括号", 20, "stack_queue", "简单", "栈匹配：右括号与栈顶配对，结束时栈为空"),
    ("用栈实现队列", 232, "stack_queue", "简单", "双栈翻转实现均摊 O(1) 的出队"),
    ("最小栈", 155, "stack_queue", "中等", "辅助栈同步记录当前最小值"),
    ("每日温度", 739, "stack_queue", "中等", "单调递减栈存下标，出栈时结算等待天数"),
    ("字符串解码", 394, "stack_queue", "中等", "栈保存外层前缀与重复次数，遇 ] 弹出拼装"),
    ("柱状图中最大的矩形", 84, "stack_queue", "困难", "单调递增栈 + 末尾补 0，出栈时以该柱为高结算"),
    # ------------------------------------------------------------- 二叉树
    ("二叉树的中序遍历", 94, "binary_tree", "简单", "递归 / 显式栈 / Morris 三种实现"),
    ("二叉树的最大深度", 104, "binary_tree", "简单", "递归：1 + max(左, 右)；或层序计数"),
    ("翻转二叉树", 226, "binary_tree", "简单", "递归交换左右子树"),
    ("二叉树的层序遍历", 102, "binary_tree", "中等", "队列 BFS，按当前层长度收集结果"),
    ("二叉树的最近公共祖先", 236, "binary_tree", "中等", "后序递归：左右子树都命中则该节点为 LCA"),
    ("验证二叉搜索树", 98, "binary_tree", "中等", "中序遍历递增，或递归传上下界"),
    ("二叉树中的最大路径和", 124, "binary_tree", "困难", "后序返回单边最大贡献，全局记录两边的和"),
    ("从前序与中序遍历序列构造二叉树", 105, "binary_tree", "中等", "哈希定位根节点，递归划分左右区间"),
    # ----------------------------------------------------------------- 堆
    ("数组中的第 K 个最大元素", 215, "heap", "中等", "小顶堆 O(nlogk) 或快速选择平均 O(n)"),
    ("前 K 个高频元素", 347, "heap", "中等", "哈希计数 + 小顶堆（或桶排序）"),
    ("数据流的中位数", 295, "heap", "困难", "大顶堆 + 小顶堆对顶，保持两堆大小平衡"),
    # --------------------------------------------------------------- 图
    ("岛屿数量", 200, "graph", "中等", "DFS/BFS/并查集标记连通块，每发现一块计数加一"),
    ("课程表", 207, "graph", "中等", "拓扑排序判环：BFS 入度法或 DFS 三色标记"),
    ("腐烂的橘子", 994, "graph", "中等", "多源 BFS：所有坏橘子初始入队，层数即分钟数"),
    # ----------------------------------------------------------- 二分查找
    ("二分查找", 704, "binary_search", "简单", "标准模板，先统一区间开闭再写循环"),
    ("搜索旋转排序数组", 33, "binary_search", "中等", "每次判断哪一半有序，再决定搜索区间"),
    ("寻找旋转排序数组中的最小值", 153, "binary_search", "中等", "与右端点比较收缩区间（元素互异前提）"),
    ("在排序数组中查找元素的第一个和最后一个位置", 34, "binary_search", "中等", "两次二分分别找左右边界"),
    # ----------------------------------------------------------- 动态规划
    ("爬楼梯", 70, "dp", "简单", "f(n) = f(n-1) + f(n-2)，滚动变量压空间"),
    ("打家劫舍", 198, "dp", "中等", "dp[i] = max(dp[i-1], dp[i-2] + nums[i])"),
    ("零钱兑换", 322, "dp", "中等", "完全背包求最少硬币数，初值 INF 标记不可达"),
    ("最长递增子序列", 300, "dp", "中等", "O(n²) DP 或贪心 + 二分维护 tails 数组"),
    ("最长回文子串", 5, "dp", "中等", "中心扩展 O(n²)，或区间 DP 枚举长度"),
    ("编辑距离", 72, "dp", "困难", "二维 DP：增/删/改三路转移取最小"),
    ("单词拆分", 139, "dp", "中等", "dp[i] 表示前 i 个字符可拆分，配合字典哈希"),
    ("分割等和子集", 416, "dp", "中等", "01 背包可行性：目标和为总和的一半"),
    # --------------------------------------------------------------- 贪心
    ("跳跃游戏", 55, "greedy", "中等", "维护可达最远下标，扫到当前边界仍不可达则失败"),
    ("跳跃游戏 II", 45, "greedy", "中等", "按层贪心：在当前覆盖范围内选能跳到最远的点"),
    ("分发糖果", 135, "greedy", "困难", "左右各扫一次满足相邻约束，取较大值"),
    ("无重叠区间", 435, "greedy", "中等", "按右端点排序，贪心选择最早结束的区间"),
    # --------------------------------------------------------------- 回溯
    ("全排列", 46, "backtracking", "中等", "回溯 + used 标记；结果收集时复制路径"),
    ("子集", 78, "backtracking", "中等", "选/不选回溯，每个节点都是答案"),
    ("组合总和", 39, "backtracking", "中等", "排序后 start 参数 + 剪枝，避免组合重复"),
    ("单词搜索", 79, "backtracking", "中等", "网格 DFS + 原地标记已访问字符"),
    ("N 皇后", 51, "backtracking", "困难", "逐行放置，列与两条对角线集合判重"),
    # --------------------------------------------------------------- 字符串
    ("最长公共前缀", 14, "string", "简单", "纵向扫描：逐个字符比较所有字符串"),
    ("字符串相乘", 43, "string", "中等", "竖式模拟：结果落在下标 i+j 与 i+j+1"),
    ("反转字符串中的单词", 151, "string", "中等", "整体反转 + 逐单词反转，或栈收集单词"),
    # --------------------------------------------------------------- 位运算
    ("只出现一次的数字", 136, "bit", "简单", "全员异或：相同的数两两抵消"),
    ("位 1 的个数", 191, "bit", "简单", "n & (n-1) 每次消掉最低位的 1"),
    ("比特位计数", 338, "bit", "简单", "dp[i] = dp[i >> 1] + (i & 1)"),
    # --------------------------------------------------------------- 前缀和
    ("和为 K 的子数组", 560, "prefix_sum", "中等", "前缀和 + 哈希计数；初始 {0: 1}"),
    ("二维区域和检索 - 矩阵不可变", 304, "prefix_sum", "中等", "二维前缀和预处理，容斥计算矩形和"),
)

PROBLEMS: tuple[AlgorithmProblem, ...] = tuple(
    AlgorithmProblem(*row) for row in _PROBLEM_ROWS
)


# ---------------------------------------------------------------------------
# 主题识别：关键词命中（确定性，命中多者优先）
# ---------------------------------------------------------------------------

_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "array": ("数组", "哈希", "字典", "出现次数", "频率", "两数", "区间", "原地"),
    "two_pointers": ("双指针", "对撞", "有序数组", "快慢指针", "回文", "去重", "原地移除"),
    "sliding_window": ("滑动窗口", "连续子数组", "连续子串", "最长子串", "最小覆盖", "无重复"),
    "linked_list": ("链表", "节点", "环", "反转", "哨兵"),
    "stack_queue": ("栈", "队列", "括号", "单调栈", "入栈", "出栈", "先进先出", "后进先出"),
    "binary_tree": ("二叉树", "二叉搜索树", "根节点", "叶子", "子树", "遍历", "前序", "中序", "后序", "层序"),
    "heap": ("第 k", "第k", "top k", "前 k", "前k", "中位数", "堆", "优先队列", "高频元素"),
    "graph": ("图", "岛屿", "连通", "最短路径", "拓扑", "网格", "课程表", "腐烂"),
    "binary_search": ("二分", "旋转排序", "第一个和最后一个", "有序", "logn"),
    "dp": ("动态规划", "方案数", "最大和", "最小代价", "子序列", "背包", "编辑距离", "爬楼梯", "打家劫舍", "状态转移"),
    "greedy": ("贪心", "跳跃", "区间调度", "最少数量", "覆盖范围"),
    "backtracking": ("所有可能", "全部方案", "排列", "组合", "子集", "皇后", "数独", "单词搜索", "回溯"),
    "string": ("字符串", "字符", "子串", "前缀", "拼接", "回文串"),
    "bit": ("异或", "位运算", "二进制", "比特", "位 1"),
    "prefix_sum": ("前缀和", "区间和", "子数组和", "累计和"),
}


def detect_topics(text: str, *, limit: int = 3) -> list[str]:
    """Rank topics by keyword hits; deterministic tie-break by topic order."""
    lowered = text.lower()
    scored: list[tuple[int, int, str]] = []
    for index, (topic, keywords) in enumerate(_TOPIC_KEYWORDS.items()):
        hits = sum(1 for keyword in keywords if keyword in lowered)
        if hits:
            scored.append((hits, index, topic))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [topic for _, _, topic in scored[: max(1, limit)]]


def resolve_topic(value: str) -> str:
    """Resolve a topic key or its Chinese label; '' when unknown."""
    cleaned = value.strip()
    if not cleaned:
        return ""
    if cleaned in TOPIC_LABELS:
        return cleaned
    for key, label in TOPIC_LABELS.items():
        if cleaned == label:
            return key
    return ""


def _keyword_hits(text: str, topic: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(
        keyword for keyword in _TOPIC_KEYWORDS.get(topic, ()) if keyword in lowered
    )


# ---------------------------------------------------------------------------
# 解题框架：每个主题的步骤 / 陷阱 / 复杂度目标 / 面试追问
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicFramework:
    """Playbook for one topic (or the generic fallback)."""

    steps: tuple[str, ...]
    pitfalls: tuple[str, ...]
    target_complexity: str
    followups: tuple[str, ...]


_FRAMEWORKS: dict[str, TopicFramework] = {
    "array": TopicFramework(
        steps=(
            "明确返回值：值 / 下标对 / 计数，是否需要保持原顺序",
            "暴力 O(n²) 起步，再找可哈希化的“查找”环节换时间",
            "涉及两数/区间时先排序或用哈希；原地题注意下标与值的对应",
            "用空、单元素、全相同、含负数四类用例自测",
        ),
        pitfalls=(
            "两数之和类：先查补数再插入当前值，否则会自匹配",
            "去重类题目漏掉排序后的相邻重复判断",
            "原地修改时覆盖了还没处理的数据",
        ),
        target_complexity="常见目标 O(n) 时间 / O(n) 空间；要求 O(1) 空间的题多需原地技巧",
        followups=(
            "如果数组已排序，解法会怎么变？",
            "数据量放大到 10⁹、内存放不下时怎么办？",
        ),
    ),
    "two_pointers": TopicFramework(
        steps=(
            "先判断类型：对撞（有序/两端逼近）还是快慢（原地去重/找环）",
            "对撞：和偏小移左、偏大移右，并说明移动不会丢解",
            "快慢：慢指针维护“已处理”区间，快指针探索",
            "每步只移动一个指针，保证单调性与 O(n)",
        ),
        pitfalls=(
            "循环边界写错：while l <= r 与 l < r 的取舍要按区间语义确定",
            "链表找环：相遇后一指针回起点同速再遇即入口",
            "去重跳过重复元素时要防越界",
        ),
        target_complexity="O(n) 时间、O(1) 额外空间（数组原地类）",
        followups=(
            "三数之和如何从双指针扩展并正确去重？",
            "为什么对撞指针不会错过最优解？",
        ),
    ),
    "sliding_window": TopicFramework(
        steps=(
            "确认单调性：窗口扩张/收缩后约束可判定（最长/最短/固定长）",
            "右指针扩张更新计数，窗口违规时收缩左指针",
            "用 need/have 计数或单调队列维护窗口内信息",
            "最短窗口类：合法时先更新答案再收缩；最长类：违规时收缩",
        ),
        pitfalls=(
            "收缩用 while 而不是 if，否则窗口无法回到合法状态",
            "计数更新顺序错误导致多算/漏算",
            "空串与全重复字符的初始化边界",
        ),
        target_complexity="O(n)（每个元素最多进出窗口一次）",
        followups=(
            "如果要求窗口内最大值呢？（单调队列）",
            "字符集不是常数时空间复杂度如何变化？",
        ),
    ),
    "linked_list": TopicFramework(
        steps=(
            "先画图定义指针语义（prev/curr/next 各自代表什么）",
            "优先用哨兵节点（dummy）简化头节点处理",
            "写循环前先确定不变量与终止条件",
            "归并/排序类先把“找中点”“合并两个子链表”拆成独立函数",
        ),
        pitfalls=(
            "修改 next 前先保存后继，否则链断",
            "快慢指针处理偶数长度链表时对中点的约定要一致",
            "LRU 类题忘记同步更新哈希表与链表两侧",
        ),
        target_complexity="多为 O(n) 时间、O(1) 空间（递归版有 O(n) 栈开销）",
        followups=(
            "能否改成递归写法？空间复杂度是多少？",
            "K 个一组翻转如何复用“区间反转”逻辑？",
        ),
    ),
    "stack_queue": TopicFramework(
        steps=(
            "识别“最近相关性”：括号匹配、下一个更大元素、表达式求值",
            "单调栈：维护递减/递增序列，新元素入栈时弹出被支配项",
            "需要延迟处理/嵌套结构时用栈保存上下文",
            "层序/最短步数类用队列 BFS",
        ),
        pitfalls=(
            "弹出与入栈顺序写反",
            "单调栈存下标与存值要统一（求距离必须存下标）",
            "空栈访问与收尾哨兵（如柱状图末尾补 0）",
        ),
        target_complexity="O(n)；单调栈中每个元素最多进出一次",
        followups=(
            "单调栈和优先队列分别适合什么场景？",
            "如何用两个栈实现均摊 O(1) 的出队？",
        ),
    ),
    "binary_tree": TopicFramework(
        steps=(
            "先确定遍历序：前序自顶向下传参，后序自底向上收集",
            "递归三要素：终止条件、单层逻辑、返回值语义",
            "需要额外状态时用闭包/成员变量，或让子树返回聚合值",
            "层序类用队列按层处理",
        ),
        pitfalls=(
            "BST 验证不能只比较父子节点，要传上下界或中序递增",
            "后序聚合题注意“贡献值为负则舍弃”",
            "空树与单节点边界漏判",
        ),
        target_complexity="O(n) 时间；递归空间 O(h)，最坏 O(n)",
        followups=(
            "递归改迭代怎么写？（显式栈 / Morris）",
            "如何在 O(1) 空间做中序遍历？",
        ),
    ),
    "heap": TopicFramework(
        steps=(
            "识别形态：Top-K / 流式中位数 / 合并 K 个有序序列",
            "Top-K 最大用大小为 k 的小顶堆；Top-K 最小反之",
            "流式中位数：对顶双堆，保持两边大小平衡",
            "先估数据量，再决定用堆还是快速选择",
        ),
        pitfalls=(
            "堆大小维持 k 时 push/pop 顺序写反",
            "比较器方向导致取到错误的 k 个元素",
            "Python heapq 是小顶堆，做大顶堆要存负数",
        ),
        target_complexity="O(n log k)（堆）或平均 O(n)（快速选择）",
        followups=(
            "海量数据 Top-K 如何分治求解？",
            "快速选择的最坏复杂度如何规避（随机化）？",
        ),
    ),
    "graph": TopicFramework(
        steps=(
            "先翻译成图：节点是什么、边是什么、有向还是无向",
            "选择遍历：BFS 最短/层次、DFS 连通性/环、拓扑排序判依赖",
            "网格题把四方向写成常量表并做越界检查",
            "递归 DFS 注意深度，必要时改迭代防爆栈",
        ),
        pitfalls=(
            "visited 标记时机（入队即标记，防重复入队）",
            "拓扑排序入度更新与环检测条件",
            "多源 BFS 要初始把所有源入队",
        ),
        target_complexity="O(V + E)；网格题 O(mn)",
        followups=(
            "并查集解法的复杂度与适用场景？",
            "如何输出最短路径而不只是长度？",
        ),
    ),
    "binary_search": TopicFramework(
        steps=(
            "确认单调性：是否存在可二分的分界",
            "统一区间约定（左闭右闭或左闭右开）后套模板",
            "循环不变量：答案始终在 [lo, hi] 内，每次收缩都排除不可能区间",
            "命中后按题意处理边界（第一个/最后一个）",
        ),
        pitfalls=(
            "mid 计算防溢出（lo + (hi - lo) // 2）",
            "收缩时不排除边界导致死循环",
            "旋转数组要先判断哪半有序再缩区间",
        ),
        target_complexity="O(log n) 时间、O(1) 空间",
        followups=(
            "有重复元素时如何找左右边界？",
            "如何对答案本身做二分（二分答案）？",
        ),
    ),
    "dp": TopicFramework(
        steps=(
            "定义状态：dp[i] / dp[i][j] 的精确语义（“以 i 结尾”还是“前 i 个”）",
            "写转移方程：枚举最后一步的全部来源，取最优或累加",
            "确定遍历顺序（依赖方向）与初始化（空串、第 0 行）",
            "按空间依赖用滚动数组压缩",
            "用暴力递归 + 记忆化交叉验证转移正确性",
        ),
        pitfalls=(
            "状态定义含糊导致重复计数或漏解",
            "01 背包倒序遍历、完全背包正序遍历别记反",
            "“以 i 结尾”与“前 i 个”的答案位置不同",
        ),
        target_complexity="状态数 × 转移代价；常用滚动数组把空间降到 O(n)",
        followups=(
            "滚动数组为什么可行？依赖方向如何判断？",
            "如果要求输出具体方案而不是最优值呢？",
        ),
    ),
    "greedy": TopicFramework(
        steps=(
            "找出贪心选择：每步保留对后续最有利的局部最优",
            "用交换论证证明任意最优解可调整为包含该贪心选择",
            "排序往往是把贪心变得可执行的钥匙",
            "区间类题明确按左端点还是右端点排序",
        ),
        pitfalls=(
            "贪心可行性未证明导致反例翻车（如面额不规整的硬币）",
            "区间调度按右端点排序而不是左端点",
            "开闭区间语义要统一",
        ),
        target_complexity="排序 O(n log n) + 线性扫描 O(n)",
        followups=(
            "本题为什么贪心可行，而动态规划解法更直观？",
            "能举一个贪心失效的反例吗？",
        ),
    ),
    "backtracking": TopicFramework(
        steps=(
            "画出决策树：每层是“选/不选”还是“枚举下一个候选”",
            "定义路径（已选）、选择列表、终止条件三要素",
            "明确收集答案的时机（叶子节点还是每个节点）",
            "排序 + start 参数控制组合去重；used 数组控制排列",
            "命中即剪枝：可行性、最优性、去重三类",
        ),
        pitfalls=(
            "不变量（路径/标记）在递归返回后必须还原",
            "组合去重漏掉“同层相同元素跳过”",
            "收集结果要复制路径而非存引用",
        ),
        target_complexity="指数级；剪枝质量决定实际规模",
        followups=(
            "如何把指数级搜索改写为 DP？",
            "N 皇后用位运算如何加速列与对角线判重？",
        ),
    ),
    "string": TopicFramework(
        steps=(
            "先明确是基于字符还是单词操作，注意不可变对象的拼接成本",
            "双指针：对撞（回文）/ 快慢（原地移除）",
            "子串类转滑动窗口；前缀类考虑 Trie",
            "模拟类（大数相乘、atoi）分解步骤并处理进位/溢出",
        ),
        pitfalls=(
            "Python 字符串不可变，循环内 += 会平方级退化",
            "回文判断漏掉非字母数字字符的清洗步骤",
            "整数溢出与符号位处理",
        ),
        target_complexity="O(n) 扫描；字符计数空间 O(字符集)",
        followups=(
            "如何判断两个字符串互为字母异位词？",
            "单词反转如何做到 O(1) 额外空间？",
        ),
    ),
    "bit": TopicFramework(
        steps=(
            "先把运算翻译成二进制视角：异或消对、与运算取位、移位枚举",
            "收集常用技巧：x & (x-1) 消最低位 1、x & -x 取最低位 1",
            "出现次数类问题考虑按位统计（32 位逐位计数）",
            "用移位与掩码实现无分支处理",
        ),
        pitfalls=(
            "负数的补码语义（Python 整数无溢出但要处理符号）",
            "异或性质：相同为 0、任何数与 0 异或为自身",
            "位运算优先级低于比较运算，必要时加括号",
        ),
        target_complexity="O(1)（固定位宽）或 O(n)（扫描）",
        followups=(
            "找出只出现两次/三次的数字怎么做？",
            "如何取最低位的 1 并把它清掉？",
        ),
    ),
    "prefix_sum": TopicFramework(
        steps=(
            "把区间和/子数组和翻译为前缀和之差：S[j] - S[i-1]",
            "与 k 相关的计数题配哈希表：边扫描边查 S[i-1] = S[j] - k",
            "二维问题用二维前缀和，或逐行压缩成一维",
            "哈希表初始化为 {0: 1} 以覆盖从头开始的子数组",
        ),
        pitfalls=(
            "忘记初始化前缀和哈希表 {0: 1}",
            "前缀和数组长度为 n+1 的下标偏移",
            "取模类问题注意负余数处理",
        ),
        target_complexity="O(n) 时间、O(n) 空间（哈希计数）",
        followups=(
            "元素可正可负时滑动窗口为什么失效？",
            "如何把二维前缀和压缩到一维？",
        ),
    ),
}

_GENERIC_FRAMEWORK = TopicFramework(
    steps=(
        "明确输入/输出与返回格式，用少量样例手推一遍",
        "从暴力解法出发找瓶颈环节，再针对性优化（哈希/双指针/二分/DP）",
        "写清边界处理：空输入、单元素、极值",
        "用反例自测并给出复杂度估算",
    ),
    pitfalls=(
        "未确认题型直接套模板",
        "忽略重复元素、负数、溢出的特判",
        "复杂度估算遗漏隐藏循环（切片、in 判断等）",
    ),
    target_complexity="先保证正确性（可回退的暴力解），再优化一个数量级",
    followups=(
        "问题的数据规模上限是多少？这决定什么复杂度可行",
        "能否给出更省空间的方案？",
    ),
)


# ---------------------------------------------------------------------------
# 选题与题目分析
# ---------------------------------------------------------------------------


def _difficulty_ok(problem: AlgorithmProblem, difficulty: str) -> bool:
    wanted = difficulty.strip()
    return wanted in ("", "混合") or problem.difficulty == wanted


def _topic_problems(topic: str, difficulty: str) -> list[AlgorithmProblem]:
    pool = [
        problem
        for problem in PROBLEMS
        if problem.topic == topic and _difficulty_ok(problem, difficulty)
    ]
    pool.sort(key=lambda problem: _DIFFICULTY_ORDER.get(problem.difficulty, 9))
    return pool


def _interleave(problems: list[AlgorithmProblem]) -> list[AlgorithmProblem]:
    """Interleave problems across topics so the filler stays varied."""
    buckets: dict[str, list[AlgorithmProblem]] = {}
    for problem in problems:
        buckets.setdefault(problem.topic, []).append(problem)
    for bucket in buckets.values():
        bucket.sort(key=lambda problem: _DIFFICULTY_ORDER.get(problem.difficulty, 9))
    interleaved: list[AlgorithmProblem] = []
    while buckets:
        for topic in list(buckets):
            interleaved.append(buckets[topic].pop(0))
            if not buckets[topic]:
                del buckets[topic]
    return interleaved


def problems_for_topic(
    topic: str, *, difficulty: str = "混合", limit: int = 5
) -> list[AlgorithmProblem]:
    """Classic problems of one topic, easiest first ('' / unknown -> [])."""
    key = resolve_topic(topic)
    if not key or limit <= 0:
        return []
    return _topic_problems(key, difficulty)[:limit]


def select_problems(
    *,
    topics: Iterable[str] | None = None,
    difficulty: str = "混合",
    limit: int = 8,
) -> list[AlgorithmProblem]:
    """Select a varied problem set: wanted topics first, then the rest.

    Matching topics come first (round-robin inside each topic, easiest
    difficulty first); every other topic then fills the remaining slots in
    an interleaved order, so a small ``limit`` never returns a single-topic
    block.
    """
    limit = max(1, limit)
    wanted: list[str] = []
    for raw_topic in topics or []:
        key = resolve_topic(str(raw_topic))
        if key and key not in wanted:
            wanted.append(key)

    result: list[AlgorithmProblem] = []
    wanted_batches: list[list[AlgorithmProblem]] = []
    for topic in wanted:
        batch = _topic_problems(topic, difficulty)
        if batch:
            wanted_batches.append(batch)
    while wanted_batches and len(result) < limit:
        for batch in list(wanted_batches):
            result.append(batch.pop(0))
            if not batch:
                wanted_batches.remove(batch)
            if len(result) >= limit:
                break

    if len(result) < limit:
        filler: list[AlgorithmProblem] = []
        for topic in TOPIC_LABELS:
            if topic not in wanted:
                filler.extend(_topic_problems(topic, difficulty))
        result.extend(_interleave(filler)[: limit - len(result)])
    return result


@dataclass(frozen=True)
class ProblemAnalysis:
    """Structured analysis result for one problem statement."""

    primary_topic: str
    primary_label: str
    matched_topics: tuple[str, ...]
    keywords: tuple[str, ...]
    steps: tuple[str, ...]
    pitfalls: tuple[str, ...]
    target_complexity: str
    followups: tuple[str, ...]
    related: tuple[AlgorithmProblem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_topic": self.primary_topic or "generic",
            "primary_label": self.primary_label,
            "matched_topics": list(self.matched_topics),
            "keywords": list(self.keywords),
            "steps": list(self.steps),
            "pitfalls": list(self.pitfalls),
            "target_complexity": self.target_complexity,
            "followups": list(self.followups),
            "related_problems": [problem.to_dict() for problem in self.related],
        }


def analyze_problem(
    text: str, *, topic: str = "", related_limit: int = 4
) -> ProblemAnalysis:
    """Classify a problem statement and assemble its topic playbook.

    ``topic`` (key or Chinese label) forces the classification; otherwise
    topics are detected from keyword hits, best-first.
    """
    explicit = resolve_topic(topic)
    if explicit:
        matched: tuple[str, ...] = (explicit,)
    else:
        matched = tuple(detect_topics(text, limit=3))
    primary = matched[0] if matched else ""
    framework = _FRAMEWORKS.get(primary, _GENERIC_FRAMEWORK)
    related = (
        tuple(problems_for_topic(primary, limit=related_limit)) if primary else ()
    )
    return ProblemAnalysis(
        primary_topic=primary,
        primary_label=TOPIC_LABELS.get(primary, "通用"),
        matched_topics=tuple(TOPIC_LABELS.get(item, item) for item in matched),
        keywords=_keyword_hits(text, primary) if primary else (),
        steps=framework.steps,
        pitfalls=framework.pitfalls,
        target_complexity=framework.target_complexity,
        followups=framework.followups,
        related=related,
    )
