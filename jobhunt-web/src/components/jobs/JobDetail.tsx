import { Button, Drawer, Descriptions, Space, Tag, Typography } from "antd";
import { ExternalLink } from "lucide-react";
import type { Job } from "../../types";
import { compactDate, salaryRange } from "../../utils/format";

export default function JobDetail({ job, open, onClose }: { job?: Job; open: boolean; onClose: () => void }) {
  return (
    <Drawer width={520} title="岗位详情" open={open} onClose={onClose}>
      {job ? (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="岗位">{job.title}</Descriptions.Item>
            <Descriptions.Item label="公司">{job.company}</Descriptions.Item>
            <Descriptions.Item label="城市">{job.city}</Descriptions.Item>
            <Descriptions.Item label="薪资">{salaryRange(job.salary_min, job.salary_max)}</Descriptions.Item>
            <Descriptions.Item label="方向">{job.direction}</Descriptions.Item>
            <Descriptions.Item label="来源">{job.source_site || "本地岗位库"}</Descriptions.Item>
            <Descriptions.Item label="获取时间">{compactDate(job.fetched_at)}</Descriptions.Item>
            <Descriptions.Item label="数据状态">{job.provenance_status || "unverified"}</Descriptions.Item>
          </Descriptions>
          <Space wrap>{(job.tags ?? []).map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space>
          {job.source_url || job.apply_url ? (
            <Button
              type="primary"
              icon={<ExternalLink size={16} />}
              href={job.apply_url || job.source_url}
              target="_blank"
              rel="noreferrer"
            >
              打开原岗位
            </Button>
          ) : null}
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
            {job.description || job.jd_text || "暂无 JD 原文"}
          </Typography.Paragraph>
        </Space>
      ) : null}
    </Drawer>
  );
}
