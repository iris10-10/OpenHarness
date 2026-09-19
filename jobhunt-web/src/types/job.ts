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
}
