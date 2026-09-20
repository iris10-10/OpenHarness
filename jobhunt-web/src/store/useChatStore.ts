import { create } from "zustand";
import type { AgentActivity, AgentSession, ChatMessage, ChatTool, PendingRequest } from "../types";

interface ChatState {
  sessions: AgentSession[];
  sessionId: string | null;
  messages: ChatMessage[];
  activities: AgentActivity[];
  pendingRequest: PendingRequest | null;
  streaming: boolean;
  status: string;
  error: string | null;
  setSessions: (sessions: AgentSession[]) => void;
  setSessionId: (sessionId: string | null) => void;
  setMessages: (messages: ChatMessage[]) => void;
  addMessage: (message: ChatMessage) => void;
  upsertMessage: (message: ChatMessage) => void;
  replaceLastUser: (message: ChatMessage) => void;
  upsertAssistantDelta: (id: string, delta: string) => void;
  startTool: (tool: ChatTool, activity: AgentActivity) => void;
  completeTool: (toolName: string, output: string, isError: boolean) => void;
  setPendingRequest: (request: PendingRequest | null) => void;
  setStreaming: (streaming: boolean) => void;
  setStatus: (status: string) => void;
  setError: (error: string | null) => void;
  resetRun: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  sessions: [],
  sessionId: null,
  messages: [],
  activities: [],
  pendingRequest: null,
  streaming: false,
  status: "",
  error: null,
  setSessions: (sessions) => set({ sessions }),
  setSessionId: (sessionId) => set({ sessionId }),
  setMessages: (messages) => set({ messages }),
  addMessage: (message) => set((state) => ({ messages: [...state.messages, message] })),
  upsertMessage: (message) =>
    set((state) => {
      const exists = state.messages.some((item) => item.id === message.id);
      return {
        messages: exists
          ? state.messages.map((item) => (item.id === message.id ? message : item))
          : [...state.messages, message],
      };
    }),
  replaceLastUser: (message) =>
    set((state) => {
      const reverseIndex = [...state.messages].reverse().findIndex((item) => item.role === "user");
      if (reverseIndex < 0) return { messages: [...state.messages, message] };
      const index = state.messages.length - reverseIndex - 1;
      const messages = [...state.messages];
      messages[index] = message;
      return { messages };
    }),
  upsertAssistantDelta: (id, delta) =>
    set((state) => {
      const existing = state.messages.find((item) => item.id === id);
      if (existing) {
        return {
          messages: state.messages.map((item) =>
            item.id === id ? { ...item, content: item.content + delta } : item,
          ),
        };
      }
      return {
        messages: [
          ...state.messages,
          { id, role: "assistant", content: delta, created_at: new Date().toISOString(), tools: [] },
        ],
      };
    }),
  startTool: (tool, activity) =>
    set((state) => ({
      activities: [...state.activities, activity],
      messages: state.messages.map((message) =>
        message.role === "assistant"
          ? { ...message, tools: [...(message.tools ?? []), tool] }
          : message,
      ),
    })),
  completeTool: (toolName, output, isError) =>
    set((state) => {
      const activities = [...state.activities];
      const reverseIndex = [...activities].reverse().findIndex(
        (item) => item.toolName === toolName && item.status === "running",
      );
      if (reverseIndex >= 0) {
        const index = activities.length - reverseIndex - 1;
        activities[index] = {
          ...activities[index],
          status: isError ? "error" : "completed",
          output,
          isError,
        };
      }
      const messages = state.messages.map((message) => {
        if (message.role !== "assistant") return message;
        return {
          ...message,
          tools: (message.tools ?? []).map((tool) =>
            tool.name === toolName && (tool.status === "running" || tool.status === "started")
              ? {
                  ...tool,
                  status: isError ? ("error" as const) : ("completed" as const),
                  output,
                  is_error: isError,
                }
              : tool,
          ),
        };
      });
      return { activities, messages };
    }),
  setPendingRequest: (pendingRequest) => set({ pendingRequest }),
  setStreaming: (streaming) => set({ streaming }),
  setStatus: (status) => set({ status }),
  setError: (error) => set({ error }),
  resetRun: () => set({ activities: [], pendingRequest: null, status: "", error: null }),
}));
