import { Button, Card, Space, Tag, Tooltip, Typography } from "antd";
import { Clock3, Eye, ExternalLink, Send } from "lucide-react";
import type { Job } from "../../types";
import { compactDate, salaryRange } from "../../utils/format";
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
        <Space size={6} wrap>
          <Tag>{job.source_site || "本地岗位库"}</Tag>
          <Typography.Text type="secondary">
            <Clock3 size={13} style={{ verticalAlign: "-2px" }} /> {compactDate(job.fetched_at)}
          </Typography.Text>
          {job.provenance_status === "stale" ? <Tag color="warning">数据过期</Tag> : null}
        </Space>
        <MatchScore value={job.match_score ?? 0} />
        <Space wrap>
          {(job.tags ?? []).slice(0, 6).map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </Space>
        <Space>
          <Button icon={<Eye size={16} />} onClick={() => onDetail(job)}>详情</Button>
          {job.source_url || job.apply_url ? (
            <Tooltip title="在浏览器中打开原岗位，投递由你手动完成">
              <Button
                icon={<ExternalLink size={16} />}
                href={job.apply_url || job.source_url}
                target="_blank"
                rel="noreferrer"
              >
                原岗位
              </Button>
            </Tooltip>
          ) : null}
          <Button type="primary" icon={<Send size={16} />} onClick={() => onApply(job)}>
            加入看板
          </Button>
        </Space>
      </Space>
    </Card>
  );
}
