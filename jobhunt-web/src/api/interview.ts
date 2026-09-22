import { apiGet, apiSend } from "./client";

export type InterviewQuestion = {
  question: string;
  category: string;
  topic: string;
  difficulty: string;
  hint: string;
};

export type InterviewEvaluation = {
  question_index: number | null;
  question: string;
  answer?: string;
  score: number;
  grade: string;
  evaluated_at: string;
  dimensions?: Array<{
    name: string;
    score: number;
    max_score: number;
    advice: string;
  }>;
  missing_keywords?: string[];
  suggestions?: string[];
};

export type InterviewSummary = {
  score: number;
  grade: string;
  answered_count: number;
  question_count: number;
  completion_rate: number;
  status: string;
  evaluated_at: string;
};

export type InterviewSession = {
  id: string;
  position: string;
  company: string;
  round: string;
  difficulty: string;
  status: string;
  created_at: string;
  completed_at?: string;
  current_index: number;
  questions: InterviewQuestion[];
  current_question: InterviewQuestion | null;
  evaluations: InterviewEvaluation[];
  summary: InterviewSummary;
  finish_reason?: string;
};

export type InterviewPracticeResponse = {
  session: InterviewSession;
  guidance: string;
  questions: InterviewQuestion[];
  next_steps: string[];
  notes?: string[];
};

export type InterviewAnswerResponse = {
  feedback: InterviewEvaluation;
  session: InterviewSession;
  next_question: InterviewQuestion | null;
  finished: boolean;
};

export function createInterviewPractice(payload: {
  company?: string;
  position?: string;
  round?: string;
  jd_text?: string;
  resume_text?: string;
  difficulty?: string;
  count?: number;
}) {
  return apiSend<InterviewPracticeResponse>("/interview/practice", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getInterviewSessions() {
  return apiGet<{ items: InterviewSession[]; total: number }>("/interview/sessions");
}

export function getInterviewSession(id: string) {
  return apiGet<{ session: InterviewSession }>(`/interview/sessions/${id}`);
}

export function submitInterviewAnswer(
  id: string,
  answer: string,
  questionIndex: number,
) {
  return apiSend<InterviewAnswerResponse>(`/interview/sessions/${id}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer, question_index: questionIndex }),
  });
}

export function finishInterview(id: string, reason = "") {
  return apiSend<{ session: InterviewSession; report: InterviewSummary }>(
    `/interview/sessions/${id}/finish`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  );
}
