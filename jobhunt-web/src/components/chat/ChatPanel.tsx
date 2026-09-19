import { Card, List, Space } from "antd";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { getChatHistory, streamChat } from "../../api/chat";
import { useChatStore } from "../../store/useChatStore";
import type { ChatMessage } from "../../types";
import ChatInput from "./ChatInput";
import MessageBubble from "./MessageBubble";
import TypingIndicator from "./TypingIndicator";

export default function ChatPanel() {
  const { data } = useQuery({ queryKey: ["chat"], queryFn: getChatHistory });
  const messages = useChatStore((state) => state.messages);
  const streaming = useChatStore((state) => state.streaming);
  const setMessages = useChatStore((state) => state.setMessages);
  const setStreaming = useChatStore((state) => state.setStreaming);

  useEffect(() => {
    if (data?.messages) setMessages(data.messages);
  }, [data, setMessages]);

  const send = async (text: string) => {
    const user: ChatMessage = { id: `local-${Date.now()}`, role: "user", content: text, created_at: new Date().toISOString() };
    const assistant: ChatMessage = { id: `stream-${Date.now()}`, role: "assistant", content: "", created_at: new Date().toISOString() };
    setMessages([...messages, user, assistant]);
    setStreaming(true);
    let content = "";
    await streamChat(
      text,
      (chunk) => {
        content += chunk;
        setMessages([...messages, user, { ...assistant, content }]);
      },
      (done) => {
        setMessages([...messages, user, done]);
        setStreaming(false);
      },
    ).catch(() => setStreaming(false));
  };

  return (
    <Card style={{ minHeight: "calc(100vh - 150px)" }}>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <List
          dataSource={messages}
          locale={{ emptyText: "开始一段求职对话" }}
          renderItem={(item) => (
            <List.Item style={{ border: 0, display: "block" }}>
              <MessageBubble message={item} />
            </List.Item>
          )}
          style={{ maxHeight: "calc(100vh - 260px)", overflow: "auto" }}
        />
        {streaming ? <TypingIndicator /> : null}
        <ChatInput disabled={streaming} onSend={send} />
      </Space>
    </Card>
  );
}
