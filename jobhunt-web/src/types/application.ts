export type ApplicationStatus = "待投递" | "已投递" | "笔试中" | "面试中" | "Offer" | "已拒绝";

export interface ApplicationRecord {
  id: string;
  company: string;
  position: string;
  channel: string;
  applied_date: string;
  status: ApplicationStatus | string;
  tags?: string[];
  notes?: string[];
  follow_up_date?: string;
  last_update_date?: string;
  job_id?: string;
}

export interface ApplicationStats {
  total: number;
  by_status: Record<ApplicationStatus, number>;
  interview_rate: number;
  offer_rate: number;
  this_week: number;
}
