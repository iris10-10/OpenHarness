import { create } from "zustand";
import type { ChatMessage } from "../types";

interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  setMessages: (messages: ChatMessage[]) => void;
  append: (message: ChatMessage) => void;
  setStreaming: (streaming: boolean) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  streaming: false,
  setMessages: (messages) => set({ messages }),
  append: (message) => set((state) => ({ messages: [...state.messages, message] })),
  setStreaming: (streaming) => set({ streaming }),
}));
