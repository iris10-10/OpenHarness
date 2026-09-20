export type ChatRole = "user" | "assistant" | "system";

export interface ChatTool {
  name: string;
  status: "running" | "completed" | "error" | "started";
  input?: Record<string, unknown>;
  output?: string;
  is_error?: boolean;
  summary?: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  created_at: string;
  tools?: ChatTool[];
}

export interface AgentSession {
  session_id: string;
  title: string;
  cwd: string;
  model: string;
  provider: string;
  message_count: number;
  created_at: number;
  updated_at: number;
  busy?: boolean;
}

export interface AgentActivity {
  id: string;
  toolName: string;
  input?: Record<string, unknown>;
  output?: string;
  status: "running" | "completed" | "error";
  isError?: boolean;
  startedAt: number;
}

export interface PendingRequest {
  request_id: string;
  kind: "permission_required" | "question_required";
  tool_name?: string;
  reason?: string;
  question?: string;
  path?: string;
  diff?: string;
  added?: number;
  removed?: number;
}
