export function salaryRange(min?: number | null, max?: number | null) {
  if (!min && !max) return "薪资面议";
  if (min && max) return `${min}K-${max}K`;
  return min ? `${min}K+` : `最高 ${max}K`;
}

export function compactDate(value?: string) {
  if (!value) return "-";
  return value.slice(0, 10);
}
