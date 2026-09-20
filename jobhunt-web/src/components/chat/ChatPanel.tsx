import { Alert, Button, Card, Empty, List, Space, Spin, Tag, Typography } from "antd";
import { Bot, CircleStop, FolderGit2, Send, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  answerPermission,
  answerQuestion,
  cancelAgent,
  createAgentSession,
  deleteAgentSession,
  getAgentMessages,
  getAgentSessions,
  streamAgentMessage,
} from "../../api/chat";
import { useChatStore } from "../../store/useChatStore";
import type { ChatMessage, PendingRequest } from "../../types";
import ActivityTimeline from "./ActivityTimeline";
import ChatInput from "./ChatInput";
import MessageBubble from "./MessageBubble";
import PermissionRequest from "./PermissionRequest";
import SessionList from "./SessionList";

const selectedSessionStorageKey = "openharness.web-agent.session";

function nowMessage(role: "user" | "assistant", content: string, id: string): ChatMessage {
  return { id, role, content, created_at: new Date().toISOString(), tools: [] };
}

export default function ChatPanel() {
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const sessions = useChatStore((state) => state.sessions);
  const sessionId = useChatStore((state) => state.sessionId);
  const messages = useChatStore((state) => state.messages);
  const activities = useChatStore((state) => state.activities);
  const pendingRequest = useChatStore((state) => state.pendingRequest);
  const streaming = useChatStore((state) => state.streaming);
  const status = useChatStore((state) => state.status);
  const error = useChatStore((state) => state.error);
  const setSessions = useChatStore((state) => state.setSessions);
  const setSessionId = useChatStore((state) => state.setSessionId);
  const setMessages = useChatStore((state) => state.setMessages);
  const addMessage = useChatStore((state) => state.addMessage);
  const upsertMessage = useChatStore((state) => state.upsertMessage);
  const replaceLastUser = useChatStore((state) => state.replaceLastUser);
  const upsertAssistantDelta = useChatStore((state) => state.upsertAssistantDelta);
  const startTool = useChatStore((state) => state.startTool);
  const completeTool = useChatStore((state) => state.completeTool);
  const setPendingRequest = useChatStore((state) => state.setPendingRequest);
  const setStreaming = useChatStore((state) => state.setStreaming);
  const setStatus = useChatStore((state) => state.setStatus);
  const setError = useChatStore((state) => state.setError);
  const resetRun = useChatStore((state) => state.resetRun);

  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === sessionId) ?? null,
    [sessions, sessionId],
  );

  const loadSession = async (nextSessionId: string) => {
    const result = await getAgentMessages(nextSessionId);
    setSessionId(nextSessionId);
    setMessages(result.messages);
    resetRun();
    window.localStorage.setItem(selectedSessionStorageKey, nextSessionId);
  };

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const result = await getAgentSessions();
        if (!mounted) return;
        let available = result.sessions;
        if (available.length === 0) {
          const created = await createAgentSession({ title: "新建会话" });
          available = [created.session];
        }
        setSessions(available);
        const saved = window.localStorage.getItem(selectedSessionStorageKey);
        const next = available.find((item) => item.session_id === saved) ?? available[0];
        await loadSession(next.session_id);
      } catch (cause) {
        if (mounted) setError(cause instanceof Error ? cause.message : "无法加载 Agent 会话");
      } finally {
        if (mounted) {
          setLoading(false);
          window.setTimeout(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }), 0);
        }
      }
    })();
    return () => {
      mounted = false;
      abortRef.current?.abort();
    };
    // Loading once also intentionally restores the session selected before refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 140;
    if (nearBottom || streaming) node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [messages, streaming]);

  const createSession = async () => {
    if (streaming) return;
    const result = await createAgentSession({ title: "新建会话" });
    setSessions([result.session, ...sessions]);
    setSessionId(result.session.session_id);
    setMessages([]);
    resetRun();
    window.localStorage.setItem(selectedSessionStorageKey, result.session.session_id);
  };

  const deleteSession = async (id: string) => {
    await deleteAgentSession(id);
    const remaining = sessions.filter((session) => session.session_id !== id);
    if (remaining.length === 0) {
      await createSession();
      return;
    }
    setSessions(remaining);
    if (id === sessionId) await loadSession(remaining[0].session_id);
  };

  const send = async (text: string) => {
    if (!sessionId || streaming || !text.trim()) return;
    const submitted = text.trim();
    setDraft("");
    setError(null);
    resetRun();
    const localUser = nowMessage("user", submitted, `local-${Date.now()}`);
    addMessage(localUser);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamAgentMessage(
        sessionId,
        submitted,
        ({ event, payload }) => {
          if (event === "user_message") {
            replaceLastUser(payload.message as ChatMessage);
          } else if (event === "assistant_delta") {
            upsertAssistantDelta(String(payload.id), String(payload.message ?? ""));
          } else if (event === "assistant_complete") {
            upsertMessage(payload.message as ChatMessage);
          } else if (event === "tool_started") {
            startTool(
              { name: String(payload.tool_name), status: "running", input: payload.tool_input },
              {
                id: `${String(payload.tool_name)}-${Date.now()}`,
                toolName: String(payload.tool_name),
                input: payload.tool_input,
                status: "running",
                startedAt: Number(payload.started_at ?? Date.now() / 1000),
              },
            );
          } else if (event === "tool_completed") {
            completeTool(
              String(payload.tool_name),
              String(payload.output ?? ""),
              Boolean(payload.is_error),
            );
          } else if (event === "permission_required" || event === "question_required") {
            setPendingRequest({ ...(payload as PendingRequest), kind: event });
          } else if (event === "status") {
            setStatus(String(payload.message ?? ""));
          } else if (event === "error") {
            setError(String(payload.message ?? "Agent 执行失败"));
            if (payload.code !== "CANCELLED") setDraft(submitted);
          } else if (event === "done") {
            setStreaming(false);
            setPendingRequest(null);
            setStatus("");
          }
        },
        controller.signal,
      );
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "Agent 执行失败");
      setDraft(submitted);
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const stop = async () => {
    if (!sessionId || !streaming) return;
    await cancelAgent(sessionId);
    abortRef.current?.abort();
    setStreaming(false);
    setPendingRequest(null);
    setStatus("已停止");
  };

  const respondToRequest = async (request: PendingRequest, allowed: boolean) => {
    await answerPermission(request.request_id, allowed);
    setPendingRequest(null);
  };

  const answer = async (request: PendingRequest, value: string) => {
    await answerQuestion(request.request_id, value);
    setPendingRequest(null);
  };

  if (loading) {
    return (
      <Card className="agent-loading">
        <Spin /> 正在恢复 Agent 会话
      </Card>
    );
  }

  return (
    <div className="agent-workspace">
      <header className="agent-workspace-header">
        <Space>
          <div className="agent-mark"><Sparkles size={17} /></div>
          <div>
            <Typography.Title level={4}>OpenHarness Agent</Typography.Title>
            <Typography.Text type="secondary">浏览器工作台</Typography.Text>
          </div>
        </Space>
        <Space wrap>
          {activeSession?.cwd ? <Tag icon={<FolderGit2 size={13} />}>{activeSession.cwd}</Tag> : null}
          <Tag color={streaming ? "processing" : "default"}>{streaming ? "运行中" : "就绪"}</Tag>
          <Tag>{activeSession?.model || "默认模型"}</Tag>
        </Space>
      </header>

      <div className="agent-main-grid">
        <aside className="agent-sidebar">
          <SessionList
            sessions={sessions}
            activeId={sessionId}
            disabled={streaming}
            onCreate={() => void createSession()}
            onSelect={(id) => void loadSession(id)}
            onDelete={(id) => void deleteSession(id)}
          />
        </aside>

        <main className="agent-conversation">
          <div className="agent-conversation-scroll" ref={scrollRef}>
            {messages.length === 0 ? (
              <Empty
                image={<Bot size={32} color="#2f7d67" />}
                description="输入问题开始一次真实 Agent 对话"
              />
            ) : (
              <List
                split={false}
                dataSource={messages}
                renderItem={(message) => (
                  <List.Item className="agent-message-row">
                    <MessageBubble message={message} />
                  </List.Item>
                )}
              />
            )}
            {status ? <Typography.Text type="secondary" className="agent-status-line"><Spin size="small" /> {status}</Typography.Text> : null}
            {error ? <Alert type="error" showIcon message={error} className="agent-error" /> : null}
          </div>
          <div className="agent-composer-area">
            {pendingRequest ? (
              <PermissionRequest
                request={pendingRequest}
                onPermission={(allowed) => void respondToRequest(pendingRequest, allowed)}
                onAnswer={(value) => void answer(pendingRequest, value)}
              />
            ) : null}
            <div className="agent-composer">
              <ChatInput value={draft} onChange={setDraft} disabled={streaming || !sessionId} onSend={send} />
              <Button
                danger={streaming}
                type={streaming ? "default" : "text"}
                icon={streaming ? <CircleStop size={17} /> : <Send size={17} />}
                aria-label={streaming ? "停止任务" : "发送消息"}
                title={streaming ? "停止任务" : "发送消息"}
                onClick={() => (streaming ? void stop() : void send(draft))}
              />
            </div>
            <Typography.Text type="secondary" className="agent-composer-hint">
              Shift + Enter 换行
            </Typography.Text>
          </div>
        </main>

        <aside className="agent-activity">
          <ActivityTimeline activities={activities} />
          <div className="agent-runtime-note">
            <Typography.Text strong>运行信息</Typography.Text>
            <Typography.Paragraph type="secondary">
              {activeSession?.provider || "OpenHarness"} · {activeSession?.message_count ?? 0} 条历史消息
            </Typography.Paragraph>
          </div>
        </aside>
      </div>
    </div>
  );
}
