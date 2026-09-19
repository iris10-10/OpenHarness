import { Button, Card, Space, Tag, Typography } from "antd";
import { Eye, Send } from "lucide-react";
import type { Job } from "../../types";
import { salaryRange } from "../../utils/format";
import MatchScore from "./MatchScore";

export default function JobCard({ job, onApply, onDetail }: { job: Job; onApply: (job: Job) => void; onDetail: (job: Job) => void }) {
  return (
    <Card className="dense-card">
      <Space direction="vertical" size={8} style={{ width: "100%" }}>
        <Space style={{ justifyContent: "space-between", width: "100%" }} align="start">
          <div>
            <Typography.Text strong>{job.title}</Typography.Text>
            <br />
            <Typography.Text type="secondary">{job.company}</Typography.Text>
          </div>
          <Tag color="green">{salaryRange(job.salary_min, job.salary_max)}</Tag>
        </Space>
        <Typography.Text type="secondary">
          {job.city || "不限"} · {job.experience || "经验不限"} · {job.education || "学历不限"}
        </Typography.Text>
        <MatchScore value={job.match_score ?? 0} />
        <Space wrap>
          {(job.tags ?? []).slice(0, 6).map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </Space>
        <Space>
          <Button icon={<Eye size={16} />} onClick={() => onDetail(job)}>详情</Button>
          <Button type="primary" icon={<Send size={16} />} onClick={() => onApply(job)}>投递</Button>
        </Space>
      </Space>
    </Card>
  );
}
