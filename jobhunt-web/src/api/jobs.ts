import { apiGet, apiSend } from "./client";
import type { Job } from "../types";

export interface JobFilters {
  query?: string;
  city?: string;
  direction?: string;
  company_type?: string;
  salary_min?: number;
  page?: number;
  page_size?: number;
}

export function getJobs(filters: JobFilters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return apiGet<{ items: Job[]; total: number; page: number; page_size: number }>(`/jobs?${params}`);
}

export function matchJobs(resume_text: string, job_ids: string[] = []) {
  return apiSend<{ items: Job[]; total: number }>("/jobs/match", {
    method: "POST",
    body: JSON.stringify({ resume_text, job_ids }),
  });
}

export function importJob(payload: Partial<Job>) {
  return apiSend<{ job: Job }>("/jobs/import", { method: "POST", body: JSON.stringify(payload) });
}
