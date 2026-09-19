import { apiGet, apiSend } from "./client";

export function getProfile() {
  return apiGet<{ profile: Record<string, unknown> }>("/profile");
}

export function updateProfile(profile: Record<string, unknown>) {
  return apiSend<{ profile: Record<string, unknown> }>("/profile", { method: "PUT", body: JSON.stringify({ profile }) });
}
