import { Progress } from "antd";

export default function MatchScore({ value = 0 }: { value?: number }) {
  const color = value >= 80 ? "#1d6f5f" : value >= 60 ? "#d48806" : "#8c8c8c";
  return <Progress percent={Math.round(value)} size="small" strokeColor={color} />;
}
