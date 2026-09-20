import { API_BASE, apiGet, apiSend } from "./client";
import type { AgentSession, ChatMessage } from "../types";

export interface AgentEvent {
  event: string;
  payload: Record<string, any>;
}

export function getAgentSessions() {
  return apiGet<{ sessions: AgentSession[] }>("/agent/sessions");
}

export function createAgentSession(payload: { title?: string; cwd?: string }) {
  return apiSend<{ session: AgentSession }>("/agent/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getAgentMessages(sessionId: string) {
  return apiGet<{ messages: ChatMessage[] }>(
    `/agent/sessions/${encodeURIComponent(sessionId)}/messages`,
  );
}

export function renameAgentSession(sessionId: string, title: string) {
  return apiSend<{ session: AgentSession }>(
    `/agent/sessions/${encodeURIComponent(sessionId)}`,
    { method: "PATCH", body: JSON.stringify({ title }) },
  );
}

export function deleteAgentSession(sessionId: string) {
  return apiSend<{ ok: boolean }>(
    `/agent/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}

export function cancelAgent(sessionId: string) {
  return apiSend<{ ok: boolean; cancelled: boolean }>(
    `/agent/sessions/${encodeURIComponent(sessionId)}/cancel`,
    { method: "POST", body: "{}" },
  );
}

export function answerPermission(
  requestId: string,
  allowed: boolean,
  reply: "once" | "always" | "reject" = allowed ? "once" : "reject",
) {
  return apiSend(`/agent/requests/${encodeURIComponent(requestId)}/permission`, {
    method: "POST",
    body: JSON.stringify({ allowed, reply }),
  });
}

export function answerQuestion(requestId: string, answer: string) {
  return apiSend(`/agent/requests/${encodeURIComponent(requestId)}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export async function streamAgentMessage(
  sessionId: string,
  message: string,
  onEvent: (event: AgentEvent) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(
    `${API_BASE}/agent/sessions/${encodeURIComponent(sessionId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, attachments: [] }),
      signal,
    },
  );
  if (!response.ok || !response.body) throw new Error(await response.text());
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = block
        .split(/\r?\n/)
        .find((line) => line.startsWith("event:"))
        ?.slice(6)
        .trim();
      const data = block
        .split(/\r?\n/)
        .find((line) => line.startsWith("data:"))
        ?.slice(5)
        .trim();
      if (!event || !data) continue;
      onEvent({ event, payload: JSON.parse(data) as Record<string, any> });
    }
  }
}
