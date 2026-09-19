import PageHeader from "../components/common/PageHeader";
import ChatPanel from "../components/chat/ChatPanel";

export default function ChatPage() {
  return (
    <div className="page">
      <PageHeader title="对话助手" description="流式输出、Markdown 渲染和工具调用摘要。" />
      <ChatPanel />
    </div>
  );
}
