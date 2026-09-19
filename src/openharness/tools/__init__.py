"""Built-in tool registration."""

from openharness.tools.agent_tool import AgentTool
from openharness.tools.algorithm_practice_tool import (
    AlgorithmAnalyzeTool,
    AlgorithmRecommendTool,
    AlgorithmReviewTool,
)
from openharness.tools.application_tracker_tool import (
    ApplicationCreateTool,
    ApplicationListTool,
    ApplicationRemindTool,
    ApplicationUpdateTool,
)
from openharness.tools.ask_user_question_tool import AskUserQuestionTool
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.bash_tool import BashTool
from openharness.tools.brief_tool import BriefTool
from openharness.tools.career_path_tool import CareerPathPlanTool
from openharness.tools.company_research_tool import (
    CompanyCultureTool,
    CompanyHistoryTool,
    CompanySearchTool,
    CompanyTechstackTool,
)
from openharness.tools.config_tool import ConfigTool
from openharness.tools.cover_letter_tool import CoverLetterGenerateTool
from openharness.tools.cron_create_tool import CronCreateTool
from openharness.tools.cron_delete_tool import CronDeleteTool
from openharness.tools.cron_list_tool import CronListTool
from openharness.tools.cron_toggle_tool import CronToggleTool
from openharness.tools.enter_plan_mode_tool import EnterPlanModeTool
from openharness.tools.enter_worktree_tool import EnterWorktreeTool
from openharness.tools.exit_plan_mode_tool import ExitPlanModeTool
from openharness.tools.exit_worktree_tool import ExitWorktreeTool
from openharness.tools.file_edit_tool import FileEditTool
from openharness.tools.file_read_tool import FileReadTool
from openharness.tools.file_write_tool import FileWriteTool
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.image_generation_tool import ImageGenerationTool
from openharness.tools.image_to_text_tool import ImageToTextTool
from openharness.tools.interview_tool import (
    InterviewFeedbackTool,
    InterviewPracticeTool,
    InterviewQuestionsTool,
)
from openharness.tools.jd_parse_tool import JDParseTool
from openharness.tools.job_match_tool import CandidateMatchTool, JobMatchTool
from openharness.tools.list_mcp_resources_tool import ListMcpResourcesTool
from openharness.tools.lsp_tool import LspTool
from openharness.tools.mcp_auth_tool import McpAuthTool
from openharness.tools.mcp_tool import McpToolAdapter
from openharness.tools.notebook_edit_tool import NotebookEditTool
from openharness.tools.rag_search_tool import (
    RAGSearchInterviewTool,
    RAGSearchJobsTool,
    RAGSearchTool,
)
from openharness.tools.read_mcp_resource_tool import ReadMcpResourceTool
from openharness.tools.remote_trigger_tool import RemoteTriggerTool
from openharness.tools.resume_tool import (
    ResumeCompareTool,
    ResumeGenerateTool,
    ResumeOptimizeTool,
    ResumeParseTool,
)
from openharness.tools.salary_tool import (
    SalaryCompareTool,
    SalaryNegotiateTool,
    SalaryQueryTool,
)
from openharness.tools.send_message_tool import SendMessageTool
from openharness.tools.skill_gap_tool import SkillGapAnalyzeTool
from openharness.tools.skill_tool import SkillTool
from openharness.tools.sleep_tool import SleepTool
from openharness.tools.task_create_tool import TaskCreateTool
from openharness.tools.task_get_tool import TaskGetTool
from openharness.tools.task_list_tool import TaskListTool
from openharness.tools.task_output_tool import TaskOutputTool
from openharness.tools.task_stop_tool import TaskStopTool
from openharness.tools.task_update_tool import TaskUpdateTool
from openharness.tools.team_create_tool import TeamCreateTool
from openharness.tools.team_delete_tool import TeamDeleteTool
from openharness.tools.todo_write_tool import TodoWriteTool
from openharness.tools.tool_search_tool import ToolSearchTool
from openharness.tools.user_profile_tool import ProfileQueryTool, ProfileUpdateTool
from openharness.tools.web_fetch_tool import WebFetchTool
from openharness.tools.web_search_tool import WebSearchTool


def create_default_tool_registry(mcp_manager=None) -> ToolRegistry:
    """Return the default built-in tool registry."""
    registry = ToolRegistry()
    for tool in (
        BashTool(),
        AskUserQuestionTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        NotebookEditTool(),
        LspTool(),
        McpAuthTool(),
        GlobTool(),
        GrepTool(),
        ImageToTextTool(),
        ImageGenerationTool(),
        SkillTool(),
        ToolSearchTool(),
        WebFetchTool(),
        WebSearchTool(),
        ConfigTool(),
        BriefTool(),
        SleepTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        TodoWriteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        CronCreateTool(),
        CronListTool(),
        CronDeleteTool(),
        CronToggleTool(),
        RemoteTriggerTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskListTool(),
        TaskStopTool(),
        TaskOutputTool(),
        TaskUpdateTool(),
        AgentTool(),
        SendMessageTool(),
        TeamCreateTool(),
        TeamDeleteTool(),
        #RAG 检索工具（Phase 1）：只读，底层依赖按需惰性加载
        RAGSearchTool(),
        RAGSearchJobsTool(),
        RAGSearchInterviewTool(),
        #求职工具（Phase 2）：本地画像/投递/会话持久化 + RAG 读写，依赖同样惰性加载
        #用户画像与 JD 解析
        ProfileUpdateTool(),
        ProfileQueryTool(),
        JDParseTool(),
        #简历
        ResumeParseTool(),
        ResumeGenerateTool(),
        ResumeOptimizeTool(),
        ResumeCompareTool(),
        #岗位匹配与技能差距
        JobMatchTool(),
        CandidateMatchTool(),
        SkillGapAnalyzeTool(),
        #投递追踪
        ApplicationCreateTool(),
        ApplicationUpdateTool(),
        ApplicationListTool(),
        ApplicationRemindTool(),
        #面试准备
        InterviewQuestionsTool(),
        InterviewPracticeTool(),
        InterviewFeedbackTool(),
        #薪资调研
        SalaryQueryTool(),
        SalaryCompareTool(),
        SalaryNegotiateTool(),
        #公司背调
        CompanySearchTool(),
        CompanyCultureTool(),
        CompanyTechstackTool(),
        CompanyHistoryTool(),
        #求职信、职业路径与算法刷题
        CoverLetterGenerateTool(),
        CareerPathPlanTool(),
        AlgorithmRecommendTool(),
        AlgorithmAnalyzeTool(),
        AlgorithmReviewTool(),
    ):
        registry.register(tool)
    if mcp_manager is not None:
        registry.register(ListMcpResourcesTool(mcp_manager))
        registry.register(ReadMcpResourceTool(mcp_manager))
        for tool_info in mcp_manager.list_tools():
            registry.register(McpToolAdapter(mcp_manager, tool_info))
    return registry


__all__ = [
    "BaseTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
