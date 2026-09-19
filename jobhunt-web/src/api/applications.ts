import { apiGet, apiSend } from "./client";
import type { ApplicationRecord, ApplicationStats } from "../types";

export function getApplications() {
  return apiGet<{ items: ApplicationRecord[]; total: number; stats: ApplicationStats }>("/applications");
}

export function getApplicationStats() {
  return apiGet<{ summary: ApplicationStats; trend: Array<{ date: string; count: number; status: string }> }>("/applications/stats");
}

export function createApplication(payload: Partial<ApplicationRecord>) {
  return apiSend<{ application: ApplicationRecord; stats: ApplicationStats }>("/applications", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateApplicationStatus(id: string, status: string) {
  return apiSend<{ application: ApplicationRecord; stats: ApplicationStats }>(`/applications/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export function deleteApplication(id: string) {
  return apiSend<{ ok: boolean }>(`/applications/${id}`, { method: "DELETE" });
}
