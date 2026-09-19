import { Card, Select, Space, Typography } from "antd";
import type { ApplicationRecord, ApplicationStatus } from "../../types";
import { APPLICATION_STATUSES } from "../../utils/constants";
import ApplicationCard from "./ApplicationCard";

export default function KanbanColumn({
  status,
  records,
  onMove,
  onDelete,
}: {
  status: ApplicationStatus;
  records: ApplicationRecord[];
  onMove: (id: string, status: ApplicationStatus) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <Card className="dense-card" title={<Space><Typography.Text strong>{status}</Typography.Text><Typography.Text type="secondary">{records.length}</Typography.Text></Space>}>
      <Space direction="vertical" style={{ width: "100%" }}>
        {records.map((record) => (
          <div key={record.id}>
            <ApplicationCard record={record} onDelete={onDelete} />
            <Select
              size="small"
              value={status}
              style={{ width: "100%", marginTop: 6 }}
              options={APPLICATION_STATUSES.map((value) => ({ value, label: `移动到 ${value}` }))}
              onChange={(next) => onMove(record.id, next)}
            />
          </div>
        ))}
      </Space>
    </Card>
  );
}
