import { Button, Card, Space, Tag, Typography } from "antd";
import { Trash2 } from "lucide-react";
import type { ApplicationRecord } from "../../types";
import { STATUS_COLORS } from "../../utils/constants";
import { compactDate } from "../../utils/format";

export default function ApplicationCard({ record, onDelete }: { record: ApplicationRecord; onDelete: (id: string) => void }) {
  const color = STATUS_COLORS[record.status as keyof typeof STATUS_COLORS] ?? "default";
  return (
    <Card className="dense-card" size="small">
      <Space direction="vertical" size={6} style={{ width: "100%" }}>
        <Space style={{ justifyContent: "space-between", width: "100%" }}>
          <Typography.Text strong>{record.company}</Typography.Text>
          <Button type="text" size="small" icon={<Trash2 size={14} />} onClick={() => onDelete(record.id)} />
        </Space>
        <Typography.Text>{record.position}</Typography.Text>
        <Tag color={color}>{record.status}</Tag>
        <Typography.Text type="secondary">投递 {compactDate(record.applied_date)}</Typography.Text>
        <Space wrap>{(record.tags ?? []).map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space>
      </Space>
    </Card>
  );
}
