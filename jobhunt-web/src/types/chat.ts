export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  created_at: string;
  tools?: Array<{ name: string; status: string; summary?: string }>;
}
