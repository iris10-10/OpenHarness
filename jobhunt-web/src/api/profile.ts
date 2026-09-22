import { apiGet, apiSend } from "./client";

export type ProfileData = Record<string, unknown>;

export type AiVisibility = {
  enabled: boolean;
  allowed_sections: string[];
  included_sections: string[];
  excluded_sections: string[];
  include_sensitive_fields: boolean;
  include_contact_fields: boolean;
  reason: string;
  profile: ProfileData;
};

export type ProfileResponse = {
  profile: ProfileData;
  ai_visibility: AiVisibility;
};

export function getProfile() {
  return apiGet<ProfileResponse>("/profile");
}

export function updateProfile(profile: ProfileData) {
  return apiSend<ProfileResponse>("/profile", {
    method: "PUT",
    body: JSON.stringify({ profile }),
  });
}

export function getProfileContextPreview() {
  return apiGet<AiVisibility>("/profile/context-preview");
}
