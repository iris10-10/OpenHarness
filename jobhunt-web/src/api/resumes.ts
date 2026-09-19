import { apiGet, apiSend } from "./client";
import type { ResumeRecord } from "../types";

export function getResumes() {
  return apiGet<{ items: ResumeRecord[]; total: number }>("/resumes");
}

export function uploadResume(file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiSend<{ resume: ResumeRecord; total: number }>("/resumes/upload", { method: "POST", body: form });
}

export function optimizeResume(id: string, resume_text: string, jd_text = "") {
  return apiSend<Record<string, unknown>>(`/resumes/${id}/optimize`, {
    method: "POST",
    body: JSON.stringify({ resume_text, jd_text }),
  });
}
