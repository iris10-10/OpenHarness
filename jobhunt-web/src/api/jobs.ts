import { apiGet, apiSend } from "./client";
import type { Job } from "../types";

export interface JobFilters {
  query?: string;
  city?: string;
  direction?: string;
  company_type?: string;
  salary_min?: number;
  salary_max?: number;
  experience?: string;
  education?: string;
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

export interface JobSyncStatus {
  account_safe_mode: boolean;
  sync_enabled: boolean;
  provider_configured: boolean;
  provider_name: string;
  provider_server: string;
  allowed_sources: Array<{ source_code: string; source_site: string; allowed_domains: string[] }>;
  limits: {
    max_results: number;
    max_pages: number;
    max_details: number;
    max_response_bytes: number;
    freshness_hours: number;
  };
  recent_runs: Array<{
    run_id: string;
    provider: string;
    source_code: string;
    tool_name: string;
    fetched_count: number;
    inserted_count: number;
    updated_count: number;
    failed_count: number;
    started_at: string;
    finished_at: string;
  }>;
}

export function getJobSyncStatus() {
  return apiGet<JobSyncStatus>("/jobs/sync/status");
}

export function syncJobs(filters: JobFilters & { limit?: number }) {
  return apiSend<{ items: Job[]; total: number; report?: Record<string, unknown>; notes: string[] }>(
    "/jobs/sync",
    {
      method: "POST",
      body: JSON.stringify({
        query: filters.query ?? "",
        city: filters.city ?? "",
        salary_min: filters.salary_min,
        salary_max: filters.salary_max,
        experience: filters.experience ?? "",
        education: filters.education ?? "",
        limit: filters.limit ?? 20,
      }),
    },
  );
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
