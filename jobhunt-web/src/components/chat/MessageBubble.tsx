import { Card, Typography } from "antd";
import ReactMarkdown from "react-markdown";
import type { ChatMessage } from "../../types";
import ToolCallCard from "./ToolCallCard";

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <Card className="dense-card" style={{ maxWidth: 760, width: "fit-content", background: isUser ? "#e8f4f0" : "#fff" }}>
        <Typography.Text type="secondary">{isUser ? "你" : "求职助手"}</Typography.Text>
        <ReactMarkdown>{message.content}</ReactMarkdown>
        {message.tools?.length ? message.tools.map((tool, index) => <ToolCallCard key={`${tool.name}-${index}`} tool={tool} />) : null}
      </Card>
    </div>
  );
}
