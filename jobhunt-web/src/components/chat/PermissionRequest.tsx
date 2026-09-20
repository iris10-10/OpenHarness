import { Button, Card, Input, Space, Typography } from "antd";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import type { PendingRequest } from "../../types";

interface Props {
  request: PendingRequest;
  onPermission: (allowed: boolean) => void;
  onAnswer: (answer: string) => void;
}

export default function PermissionRequest({ request, onPermission, onAnswer }: Props) {
  const [answer, setAnswer] = useState("");
  if (request.kind === "question_required") {
    return (
      <Card size="small" className="agent-request-card">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text strong>{request.question || "Agent 需要你的回答"}</Typography.Text>
          <Space.Compact style={{ width: "100%" }}>
            <Input value={answer} onChange={(event) => setAnswer(event.target.value)} onPressEnter={() => onAnswer(answer)} />
            <Button type="primary" disabled={!answer.trim()} onClick={() => onAnswer(answer)}>回答</Button>
          </Space.Compact>
        </Space>
      </Card>
    );
  }
  return (
    <Card size="small" className="agent-request-card">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space>
          <ShieldAlert size={18} color="#b7791f" />
          <Typography.Text strong>需要确认工具权限</Typography.Text>
        </Space>
        <Typography.Text>
          {request.tool_name || "工具"}：{request.reason || "该操作会修改本地数据"}
        </Typography.Text>
        {request.path ? <Typography.Text type="secondary">{request.path}</Typography.Text> : null}
        {request.diff ? <pre className="agent-diff-output">{request.diff}</pre> : null}
        <Space>
          <Button type="primary" onClick={() => onPermission(true)}>允许一次</Button>
          <Button onClick={() => onPermission(false)}>拒绝</Button>
        </Space>
      </Space>
    </Card>
  );
}
