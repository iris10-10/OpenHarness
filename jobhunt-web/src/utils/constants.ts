import type { ApplicationStatus } from "../types";

export const APPLICATION_STATUSES: ApplicationStatus[] = ["待投递", "已投递", "笔试中", "面试中", "Offer", "已拒绝"];

export const STATUS_COLORS: Record<ApplicationStatus, string> = {
  待投递: "default",
  已投递: "processing",
  笔试中: "warning",
  面试中: "geekblue",
  Offer: "success",
  已拒绝: "error",
};
