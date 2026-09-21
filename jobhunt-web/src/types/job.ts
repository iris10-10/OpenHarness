export interface Job {
  id: string;
  title: string;
  company: string;
  city: string;
  salary_min?: number | null;
  salary_max?: number | null;
  experience?: string;
  education?: string;
  direction?: string;
  company_type?: string;
  tags?: string[];
  match_score?: number;
  posted_date?: string;
  jd_text?: string;
  url?: string;
  description?: string;
  source_code?: string;
  source_site?: string;
  source_url?: string;
  apply_url?: string;
  provider?: string;
  provider_record_id?: string;
  published_at?: string;
  fetched_at?: string;
  last_seen_at?: string;
  provenance_status?: "verified" | "unverified" | "stale" | "invalid" | string;
  status?: string;
}
