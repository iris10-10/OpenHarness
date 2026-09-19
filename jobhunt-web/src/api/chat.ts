import { API_BASE, apiGet } from "./client";
import type { ChatMessage } from "../types";

export function getChatHistory() {
  return apiGet<{ messages: ChatMessage[] }>("/chat/history");
}

export async function clearChat() {
  await fetch(`${API_BASE}/chat/clear`, { method: "DELETE" });
}

export async function streamChat(message: string, onDelta: (chunk: string) => void, onDone: (message: ChatMessage) => void) {
  const response = await fetch(`${API_BASE}/chat/send`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok || !response.body) throw new Error(await response.text());
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const raw of events) {
      const event = raw.split("\n").find((line) => line.startsWith("event:"))?.replace("event:", "").trim();
      const data = raw.split("\n").find((line) => line.startsWith("data:"))?.replace("data:", "").trim();
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === "delta") onDelta(payload.delta);
      if (event === "done") onDone(payload.message);
    }
  }
}
