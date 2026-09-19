import { apiSend } from "./client";

export function createInterviewPractice(payload: { company?: string; position?: string; round?: string; jd_text?: string; count?: number }) {
  return apiSend<Record<string, unknown>>("/interview/practice", { method: "POST", body: JSON.stringify(payload) });
}
