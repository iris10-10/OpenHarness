export interface ResumeRecord {
  id: string;
  source: string;
  name: string;
  raw_text: string;
  resume: {
    personal_info: Record<string, string>;
    skills: { technical: string[]; soft: string[]; languages: string[] };
    experience: Array<Record<string, unknown>>;
    education: Array<Record<string, unknown>>;
    ats_score: number;
  };
  ats: {
    total: number;
    components: Array<{ name: string; score: number; max_score: number; detail: string }>;
  };
}
