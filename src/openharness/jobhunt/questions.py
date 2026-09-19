"""Deterministic interview-question engine for the Phase 2 interview tools.

Two pieces live here:

- a curated high-frequency question bank spanning the categories the plan
  requires — 八股文 / 算法 / 系统设计 / 行为 / HR (项目深挖 questions are
  generated from the candidate's skills) — with a one-line answer hint per
  question so tool output stays actionable;
- :func:`build_questions`, which maps JD/resume skills and the interview
  round onto that bank: skill-matched topics first, then category
  round-robin so every round gets a realistic mix.

Real questions retrieved from the RAG ``interview`` collection always rank
first and are merged by ``interview_tool.py``; this module only owns the
deterministic fallback described in the plan (JD keywords → bank).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from openharness.jobhunt.parsing import normalize_skill

__all__ = [
    "DIFFICULTIES",
    "ROUNDS",
    "ROUND_GUIDANCE",
    "InterviewQuestion",
    "build_questions",
]

#题目的难度档位（"混合" 表示不筛选）
DIFFICULTIES: tuple[str, ...] = ("基础", "进阶", "困难")

#面试轮次 -> 覆盖的题型（"项目" 类由候选人技能动态生成）
ROUNDS: dict[str, tuple[str, ...]] = {
    "技术": ("八股文", "算法", "系统设计", "项目"),
    "行为": ("行为", "项目"),
    "HR": ("HR", "行为"),
    "高管": ("系统设计", "行为", "HR"),
}

ROUND_GUIDANCE: dict[str, str] = {
    "技术": (
        "技术面通常 45-60 分钟：自我介绍后用 JD 相关技能做八股问答，"
        "穿插项目深挖，最后 1-2 道算法或设计题。答题按“结论→原理→实践→权衡”组织。"
    ),
    "行为": (
        "行为面聚焦真实经历：用 STAR（背景-任务-行动-结果）结构作答，"
        "准备 3-5 个可复用故事（成就/冲突/失败/协作），每个都能量化。"
    ),
    "HR": (
        "HR 面考察动机、稳定性与匹配度：真诚不背稿；提前统一离职原因、"
        "职业规划、薪资区间三类问题的口径，用事实佐证。"
    ),
    "高管": (
        "高管面更看格局与判断力：准备对行业趋势、业务取舍、技术演进的独立观点，"
        "并给出你如何影响团队与结果的案例。"
    ),
}

#技能（规范名）-> 题库主题；未覆盖的技能走“项目深挖”模板题
_TOPIC_BY_SKILL: dict[str, str] = {
    "Python": "python",
    "Java": "java",
    "JVM": "java",
    "Spring": "java",
    "MyBatis": "java",
    "Dubbo": "java",
    "Netty": "java",
    "Go": "golang",
    "JavaScript": "frontend",
    "TypeScript": "frontend",
    "React": "frontend",
    "Vue": "frontend",
    "Angular": "frontend",
    "Node.js": "frontend",
    "MySQL": "mysql",
    "SQL": "mysql",
    "PostgreSQL": "mysql",
    "Oracle": "mysql",
    "Redis": "redis",
    "Memcached": "redis",
    "Linux": "os",
    "Shell": "os",
    "Kafka": "system_design",
    "RabbitMQ": "system_design",
    "RocketMQ": "system_design",
    "Docker": "system_design",
    "Kubernetes": "system_design",
    "云原生": "system_design",
    "微服务": "system_design",
    "分布式系统": "system_design",
    "高并发": "system_design",
    "高可用": "system_design",
    "系统设计": "system_design",
    "性能优化": "system_design",
}

_PROJECT_TEMPLATES: tuple[str, ...] = (
    "请介绍一个你在项目中使用 {skill} 的场景：背景、你的方案和量化结果是什么？",
    "你在项目中如何使用 {skill}？遇到过哪些难点，如何定位并解决的？",
    "如果让你重新设计一次用到 {skill} 的模块，你会做哪些改进？为什么？",
)

_PROJECT_HINT = "用 STAR 结构作答：背景→任务→你的行动→量化结果；重点讲技术选型的取舍与踩坑。"


@dataclass(frozen=True)
class InterviewQuestion:
    """One interview question with its topic, difficulty and answer hint."""

    question: str
    category: str
    topic: str
    difficulty: str
    hint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "question": self.question,
            "category": self.category,
            "topic": self.topic,
            "difficulty": self.difficulty,
            "hint": self.hint,
        }


# ---------------------------------------------------------------------------
# 高频题库：(题目, 题型, 主题, 难度, 答题要点)
# ---------------------------------------------------------------------------

_BANK_ROWS: tuple[tuple[str, str, str, str, str], ...] = (
    # ------------------------------------------------------------- Python
    (
        "Python 的 GIL 是什么？它对多线程程序有什么影响？",
        "八股文",
        "python",
        "基础",
        "全局解释器锁：同一时刻只有一个线程执行字节码；CPU 密集用多进程，IO 密集多线程仍有效",
    ),
    (
        "谈谈 Python 的内存管理与垃圾回收机制。",
        "八股文",
        "python",
        "进阶",
        "引用计数为主，标记-清除与分代回收处理循环引用；gc 模块可手动干预；注意 __del__ 的坑",
    ),
    (
        "列表、元组、字典的底层实现有什么区别？",
        "八股文",
        "python",
        "基础",
        "list 是动态数组（均摊 O(1) 追加）；tuple 不可变；dict 是哈希表且 3.7+ 保证插入序",
    ),
    (
        "装饰器的原理是什么？functools.wraps 解决什么问题？",
        "八股文",
        "python",
        "进阶",
        "高阶函数 + 闭包；@decorator 等价于 f = decorator(f)；wraps 保留 __name__/__doc__ 元信息",
    ),
    (
        "asyncio 的事件循环如何工作？同步代码里如何调用异步函数？",
        "八股文",
        "python",
        "进阶",
        "单线程事件循环 + 协程状态机；await 挂起让出控制权；run_in_executor 包装阻塞调用",
    ),
    (
        "Python 中深拷贝与浅拷贝有什么区别？",
        "八股文",
        "python",
        "基础",
        "copy.copy 只复制最外层引用；copy.deepcopy 递归复制并处理循环引用；可自定义魔术方法",
    ),
    # --------------------------------------------------------------- Java
    (
        "JVM 的内存区域如何划分？对象在堆中是如何分配与回收的？",
        "八股文",
        "java",
        "进阶",
        "堆/虚拟机栈/本地方法栈/方法区/程序计数器；TLAB 分配、Eden→Survivor→老年代；可达性分析",
    ),
    (
        "HashMap 的实现原理是什么？扩容与红黑树转换的条件？",
        "八股文",
        "java",
        "基础",
        "数组+链表+红黑树；负载因子 0.75；链表长度≥8 且容量≥64 才树化；扩容时按高低链拆分",
    ),
    (
        "synchronized 与 ReentrantLock 有什么区别？",
        "八股文",
        "java",
        "进阶",
        "monitor 实现 vs AQS；公平锁/条件变量/可中断；锁升级：偏向锁→轻量级锁→重量级锁",
    ),
    (
        "线程池的核心参数有哪些？提交任务后的执行流程是什么？",
        "八股文",
        "java",
        "进阶",
        "core/max/queue/keepAlive/拒绝策略；先核心线程→入队→扩到最大→拒绝；四种拒绝策略的取舍",
    ),
    (
        "Spring 的 IoC 与 AOP 是如何实现的？",
        "八股文",
        "java",
        "基础",
        "容器管理 Bean 生命周期与依赖注入；AOP 用动态代理：JDK 接口代理 vs CGLIB 子类代理",
    ),
    (
        "什么是类加载的双亲委派机制？为什么需要它？",
        "八股文",
        "java",
        "进阶",
        "Bootstrap→Platform→Application 逐级委派；保证核心类库安全唯一；SPI/热部署会打破委派",
    ),
    # ----------------------------------------------------------------- Go
    (
        "Go 的 goroutine 与 GMP 调度模型如何工作？",
        "八股文",
        "golang",
        "进阶",
        "G/M/P 三角色；本地队列 + 全局队列 + work stealing；sysmon 抢占；channel/网络轮询让出",
    ),
    (
        "Go 的 slice 与 map 底层结构是什么？扩容策略如何？",
        "八股文",
        "golang",
        "基础",
        "slice 是 (ptr,len,cap) 视图，append 超容按约 1.25~2 倍扩容；map 哈希桶 + 渐进式扩容",
    ),
    (
        "Go 的 channel 底层如何实现？关闭 channel 有哪些注意事项？",
        "八股文",
        "golang",
        "进阶",
        "hchan：环形缓冲 + 发送/接收等待队列；关闭后仍可读取剩余值；向已关闭 channel 发送会 panic",
    ),
    (
        "Go 的内存逃逸分析是什么？如何减少堆分配？",
        "八股文",
        "golang",
        "进阶",
        "编译器决定变量在栈还是堆；返回指针、闭包捕获、interface 装箱常逃逸；用 gcflags=-m 分析",
    ),
    # ----------------------------------------------------------- 前端基础
    (
        "JavaScript 的事件循环与微任务/宏任务如何工作？",
        "八股文",
        "frontend",
        "基础",
        "调用栈 + 任务队列；Promise.then 微任务优先于 setTimeout 宏任务；渲染时机与 rAF",
    ),
    (
        "闭包是什么？常见的使用场景与内存风险？",
        "八股文",
        "frontend",
        "基础",
        "函数 + 词法作用域引用；防抖节流、模块封装、柯里化；长生命周期引用导致内存泄漏",
    ),
    (
        "React 的虚拟 DOM 与 diff 算法思路？key 的作用是什么？",
        "八股文",
        "frontend",
        "进阶",
        "同层比较，类型不同直接替换；列表 diff 依赖 key 复用节点；避免用 index 作 key 的场景",
    ),
    (
        "浏览器缓存机制：强缓存与协商缓存如何配合？",
        "八股文",
        "frontend",
        "进阶",
        "Cache-Control/Expires 决定强缓存；ETag/Last-Modified 协商；Cache-Control 优先级更高",
    ),
    (
        "从输入 URL 到页面渲染完成，浏览器做了什么？",
        "八股文",
        "frontend",
        "进阶",
        "DNS→TCP/TLS→HTTP→HTML 解析→DOM/CSSOM→布局→绘制；关注阻塞资源与首屏优化点",
    ),
    # ---------------------------------------------------------------- MySQL
    (
        "InnoDB 与 MyISAM 有什么区别？为什么默认用 InnoDB？",
        "八股文",
        "mysql",
        "基础",
        "事务/行锁/外键/崩溃恢复（redo log）；聚簇索引 vs 非聚簇；并发写入与一致性要求",
    ),
    (
        "为什么索引用 B+ 树而不是 B 树或哈希？",
        "八股文",
        "mysql",
        "基础",
        "B+ 树非叶只存键、树更矮 IO 更少；叶节点链表支持范围查询；哈希不支持范围与排序",
    ),
    (
        "事务的 ACID 如何保证？MVCC 的实现原理是什么？",
        "八股文",
        "mysql",
        "进阶",
        "undo log 版本链 + ReadView；RC 每条语句生成快照，RR 事务首次快照；间隙锁防幻读",
    ),
    (
        "回表、覆盖索引、最左前缀原则分别是什么？",
        "八股文",
        "mysql",
        "进阶",
        "二级索引查到主键再回聚簇索引取数；查询列全在索引中免回表；联合索引按最左列优先匹配",
    ),
    (
        "慢查询如何排查与优化？explain 重点看哪些字段？",
        "八股文",
        "mysql",
        "进阶",
        "慢查询日志定位；关注 type/key/rows/Extra；索引失效场景（函数、隐式转换）；深分页优化",
    ),
    (
        "MySQL 有哪些锁？如何避免死锁？",
        "八股文",
        "mysql",
        "困难",
        "全局/表/行锁与意向锁、间隙锁与临键锁；固定顺序加锁、缩短事务、避免大范围更新",
    ),
    # ---------------------------------------------------------------- Redis
    (
        "Redis 常用数据结构有哪些？各自的典型应用场景？",
        "八股文",
        "redis",
        "基础",
        "String 缓存/计数；Hash 对象；List 队列；Set 去重；ZSet 排行榜；Bitmap/HLL 统计",
    ),
    (
        "缓存穿透、击穿、雪崩的区别与应对方案？",
        "八股文",
        "redis",
        "基础",
        "穿透：布隆过滤器/空值缓存；击穿：互斥锁/逻辑过期；雪崩：随机过期+多级缓存+限流降级",
    ),
    (
        "Redis 的持久化机制 RDB 与 AOF 如何选择？",
        "八股文",
        "redis",
        "进阶",
        "RDB 快照恢复快但可能丢数据；AOF 更安全，everysec 折中；4.0+ 支持混合持久化",
    ),
    (
        "Redis 分布式锁如何实现？有哪些坑？",
        "八股文",
        "redis",
        "进阶",
        "SET NX EX + 唯一值；Lua 脚本校验后释放；锁续期；Redlock 争议在于时钟漂移",
    ),
    (
        "Redis 的过期删除与内存淘汰策略有哪些？",
        "八股文",
        "redis",
        "进阶",
        "惰性删除 + 定期抽样删除；LRU/LFU/随机/TTL，volatile- 与 allkeys- 前缀的选择",
    ),
    (
        "Redis 主从复制、哨兵与集群的原理是什么？",
        "八股文",
        "redis",
        "困难",
        "全量+增量复制与 repl backlog；哨兵主观/客观下线；Cluster 16384 槽与 MOVED 重定向",
    ),
    # ------------------------------------------------------------ 操作系统
    (
        "进程与线程有什么区别？协程为什么更轻量？",
        "八股文",
        "os",
        "基础",
        "资源分配 vs 调度单位；独立地址空间 vs 共享；协程用户态切换、无内核参与",
    ),
    (
        "进程间通信有哪些方式？各自适用什么场景？",
        "八股文",
        "os",
        "进阶",
        "管道/消息队列/共享内存/信号量/信号/Socket；共享内存最快但需同步；Socket 可跨主机",
    ),
    (
        "死锁产生的四个必要条件是什么？如何预防？",
        "八股文",
        "os",
        "基础",
        "互斥/持有并等待/不可剥夺/循环等待；破坏任一条件：资源有序分配、一次性申请、可剥夺",
    ),
    (
        "虚拟内存与分页机制是什么？缺页中断的处理流程？",
        "八股文",
        "os",
        "进阶",
        "页表映射虚拟→物理，TLB 加速；缺页：查页表→中断→换入页面→更新页表；LRU/Clock 置换",
    ),
    (
        "IO 多路复用 select、poll、epoll 有什么区别？",
        "八股文",
        "os",
        "进阶",
        "select 有 fd 上限且全量拷贝；poll 去掉上限仍轮询；epoll 事件驱动 + 就绪列表，LT/ET 模式",
    ),
    # -------------------------------------------------------------- 网络
    (
        "TCP 三次握手与四次挥手的过程？为什么需要 TIME_WAIT？",
        "八股文",
        "network",
        "基础",
        "SYN→SYN+ACK→ACK；四次挥手各自回收；TIME_WAIT 2MSL 保证旧包消散且最终 ACK 可达",
    ),
    (
        "TCP 如何保证可靠传输？拥塞控制有哪些算法？",
        "八股文",
        "network",
        "进阶",
        "序号/确认/重传/滑动窗口/校验和；慢启动、拥塞避免、快重传、快恢复",
    ),
    (
        "HTTP/1.1、HTTP/2、HTTP/3 的核心区别是什么？",
        "八股文",
        "network",
        "进阶",
        "1.1 队头阻塞与多连接；2.0 多路复用+头部压缩；3.0 基于 QUIC/UDP 解决 TCP 层队头阻塞",
    ),
    (
        "HTTPS 的握手过程？对称与非对称加密如何配合？",
        "八股文",
        "network",
        "进阶",
        "证书验证身份与公钥；ECDHE 协商会话密钥（前向安全）；对称加密传输业务数据",
    ),
    (
        "DNS 解析的完整过程？如何优化解析延迟？",
        "八股文",
        "network",
        "基础",
        "浏览器→系统→本地 DNS→根/顶级/权威；递归与迭代；缓存层级、HTTPDNS、预解析",
    ),
    # -------------------------------------------------------------- 算法
    (
        "如何判断链表是否有环？如何找到环的入口节点？",
        "算法",
        "algorithm",
        "基础",
        "快慢指针相遇判环；相遇后一指针回到头部同速前进，再次相遇处即入口（附数学推导）",
    ),
    (
        "常见排序算法的时间复杂度、空间复杂度与稳定性？",
        "算法",
        "algorithm",
        "基础",
        "快排平均 O(nlogn) 不稳定；归并稳定但 O(n) 空间；堆排不稳定；冒泡/插入稳定",
    ),
    (
        "二叉树的前中后序与层序遍历如何实现（递归与迭代）？",
        "算法",
        "algorithm",
        "基础",
        "递归本质是栈；迭代用显式栈；层序用队列 BFS；可延伸 Morris 遍历 O(1) 空间",
    ),
    (
        "动态规划的一般解题思路？以背包问题说明状态定义。",
        "算法",
        "algorithm",
        "进阶",
        "状态定义→转移方程→初始化→遍历顺序→答案位置；01 背包与完全背包的遍历顺序差异",
    ),
    (
        "滑动窗口和双指针分别适合什么类型的问题？",
        "算法",
        "algorithm",
        "进阶",
        "窗口：连续子数组/子串的最值；双指针：有序数组两数之和、去重、链表定位",
    ),
    (
        "Top-K 问题有哪些解法？各自的复杂度与适用场景？",
        "算法",
        "algorithm",
        "进阶",
        "排序 O(nlogn)；小顶堆 O(nlogk)；快速选择平均 O(n)；海量数据分治 + 堆",
    ),
    (
        "BFS 与 DFS 的适用场景？如何避免重复访问？",
        "算法",
        "algorithm",
        "基础",
        "BFS 求最短路径/层序；DFS 做回溯/连通性；visited 集合或原地标记防止死循环",
    ),
    # ------------------------------------------------------------ 系统设计
    (
        "如何设计一个短链接系统？",
        "系统设计",
        "system_design",
        "进阶",
        "发号器/哈希生成短码；映射存 Redis+DB；302 vs 301 的选择；热点与统计",
    ),
    (
        "如何设计秒杀系统？关键难点有哪些？",
        "系统设计",
        "system_design",
        "困难",
        "网关限流 + 库存预扣；Redis 原子扣减、异步下单削峰；防超卖、幂等与对账兜底",
    ),
    (
        "如何设计一个分布式 ID 生成器？",
        "系统设计",
        "system_design",
        "进阶",
        "UUID/数据库自增/号段模式/雪花算法；趋势递增；时钟回拨的处理策略",
    ),
    (
        "如何保证缓存与数据库的一致性？",
        "系统设计",
        "system_design",
        "进阶",
        "Cache Aside：先更新库再删缓存；延迟双删；订阅 binlog 异步补偿；接受最终一致",
    ),
    (
        "如何设计一个消息队列？如何保证消息不丢失与消费幂等？",
        "系统设计",
        "system_design",
        "困难",
        "分段存储+顺序写+索引；生产 acks、副本持久化、手动提交；幂等键/去重表",
    ),
    (
        "高可用架构中限流、熔断、降级如何配合？",
        "系统设计",
        "system_design",
        "进阶",
        "限流：令牌桶/漏桶保护入口；熔断：错误率触发快速失败；降级：兜底数据；监控告警闭环",
    ),
    (
        "如何设计一个读多写少的系统？",
        "系统设计",
        "system_design",
        "进阶",
        "多级缓存（本地+Redis）；读写分离；CDN 与静态化；热点探测与本地缓存兜底",
    ),
    (
        "Docker 与 Kubernetes 的核心原理？服务如何部署上线？",
        "系统设计",
        "system_design",
        "进阶",
        "镜像分层 + namespace/cgroup 隔离；Pod/Deployment/Service；滚动更新与健康探针",
    ),
    (
        "如何设计分布式事务方案？",
        "系统设计",
        "system_design",
        "困难",
        "2PC/TCC/本地消息表/Saga；优先最终一致；补偿、幂等与对账设计",
    ),
    # -------------------------------------------------------------- 行为面
    (
        "请用 STAR 法则介绍你最有成就感的一个项目。",
        "行为",
        "behavior",
        "基础",
        "背景-任务-行动-结果；突出个人贡献与量化结果；准备 2 分钟版与 30 秒版",
    ),
    (
        "讲一次你和同事产生分歧并最终解决的经历。",
        "行为",
        "behavior",
        "基础",
        "对事不对人；用数据与方案说服；必要时升级决策；事后复盘形成机制",
    ),
    (
        "讲一次项目失败或延期的经历，你是如何复盘与改进的？",
        "行为",
        "behavior",
        "进阶",
        "坦诚失败原因；补救措施与优先级取舍；沉淀机制防止再犯，展示成长",
    ),
    (
        "线上出现紧急故障时你是如何应对的？请举例说明。",
        "行为",
        "behavior",
        "进阶",
        "先止损（回滚/降级）再定位；同步信息与分工；事后 root cause 与复盘文档",
    ),
    (
        "你是如何学习一项新技术并落地到工作中的？",
        "行为",
        "behavior",
        "基础",
        "学习路径与资料；小范围试点验证；用指标证明收益；推广与文档沉淀",
    ),
    # ----------------------------------------------------------------- HR
    (
        "请做一个简单的自我介绍。",
        "HR",
        "hr",
        "基础",
        "1-2 分钟：现状→经历亮点→与岗位的匹配点→表达意愿；避免复述简历全部内容",
    ),
    (
        "你的职业规划是什么？未来 3-5 年有什么目标？",
        "HR",
        "hr",
        "基础",
        "结合岗位谈技术与业务目标；体现稳定性与成长性；避免空泛口号",
    ),
    (
        "为什么选择我们公司/这个岗位？",
        "HR",
        "hr",
        "基础",
        "行业/产品/技术栈/团队的匹配点；展现做过功课的具体细节；与职业发展呼应",
    ),
    (
        "你的期望薪资是多少？如何得出这个数字？",
        "HR",
        "hr",
        "基础",
        "给区间不轻易给底价；依据市场数据+当前总包+岗位预算；说明可谈部分",
    ),
    (
        "你如何看待加班？",
        "HR",
        "hr",
        "基础",
        "接受合理强度与关键期冲刺；强调效率与结果导向；可反问团队实际节奏",
    ),
    (
        "你离职/看机会的原因是什么？",
        "HR",
        "hr",
        "基础",
        "聚焦发展诉求不吐槽前团队；与应聘岗位形成呼应；真实且体面",
    ),
    (
        "你手上还有其他 Offer 吗？你会如何选择？",
        "HR",
        "hr",
        "进阶",
        "如实但不交底；表达对该岗位的偏好与理由；给出时间线促成决策",
    ),
    (
        "你最大的优点和缺点分别是什么？",
        "HR",
        "hr",
        "基础",
        "优点配案例；缺点选真实但不致命的，并说明正在采取的改进措施",
    ),
)

_BANK: tuple[InterviewQuestion, ...] = tuple(
    InterviewQuestion(*row) for row in _BANK_ROWS
)


def _interleave_by_topic(questions: list[InterviewQuestion]) -> list[InterviewQuestion]:
    """Interleave questions across topics so the mix stays varied."""
    buckets: dict[str, list[InterviewQuestion]] = {}
    for question in questions:
        buckets.setdefault(question.topic, []).append(question)
    interleaved: list[InterviewQuestion] = []
    while buckets:
        for topic in list(buckets):
            interleaved.append(buckets[topic].pop(0))
            if not buckets[topic]:
                del buckets[topic]
    return interleaved


def _project_questions(skills: list[str]) -> list[InterviewQuestion]:
    """Generate project-deep-dive questions from the candidate's skills."""
    questions: list[InterviewQuestion] = []
    for index, skill in enumerate(skills[:3]):
        template = _PROJECT_TEMPLATES[index % len(_PROJECT_TEMPLATES)]
        questions.append(
            InterviewQuestion(
                question=template.format(skill=skill),
                category="项目",
                topic="project",
                difficulty="进阶",
                hint=_PROJECT_HINT,
            )
        )
    return questions


def build_questions(
    skills: Iterable[str],
    *,
    round_kind: str = "技术",
    difficulty: str = "混合",
    limit: int = 8,
) -> list[InterviewQuestion]:
    """Select a deterministic question set for one round and skill profile.

    Skill-matched topics come first inside each category; categories are
    then interleaved round-robin (八股文 → 算法 → 系统设计 → 项目 → …) and
    truncated to ``limit`` so every round gets a realistic mix.
    """
    categories = ROUNDS.get(round_kind, ROUNDS["技术"])
    limit = max(1, limit)

    canonical: list[str] = []
    for skill in skills:
        name = normalize_skill(skill)
        if name and name not in canonical:
            canonical.append(name)
    topics: list[str] = []
    for name in canonical:
        topic = _TOPIC_BY_SKILL.get(name)
        if topic and topic not in topics:
            topics.append(topic)

    want_difficulty = difficulty.strip()

    def difficulty_ok(question: InterviewQuestion) -> bool:
        return want_difficulty in ("", "混合") or question.difficulty == want_difficulty

    batches: list[list[InterviewQuestion]] = []
    for category in categories:
        if category == "项目":
            batch = _project_questions(canonical)
        else:
            pool = [q for q in _BANK if q.category == category and difficulty_ok(q)]
            matched = [q for q in pool if q.topic in topics]
            matched.sort(key=lambda q: topics.index(q.topic))
            others = [q for q in pool if q not in matched]
            batch = matched + _interleave_by_topic(others)
        if batch:
            batches.append(batch)

    result: list[InterviewQuestion] = []
    while batches and len(result) < limit:
        for batch in list(batches):
            result.append(batch.pop(0))
            if not batch:
                batches.remove(batch)
            if len(result) >= limit:
                break
    return result
