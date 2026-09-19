import { Spin, Typography } from "antd";

export default function TypingIndicator() {
  return (
    <Typography.Text type="secondary">
      <Spin size="small" /> 正在生成
    </Typography.Text>
  );
}
