import { Alert, App, Button, List, Pagination, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { createApplication } from "../api/applications";
import { getJobSyncStatus, getJobs, syncJobs, type JobFilters } from "../api/jobs";
import PageHeader from "../components/common/PageHeader";
import JobCard from "../components/jobs/JobCard";
import JobDetail from "../components/jobs/JobDetail";
import JobFilter from "../components/jobs/JobFilter";
import type { Job } from "../types";
import { RefreshCw } from "lucide-react";

export default function JobsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState<JobFilters>(() => ({
    company: searchParams.get("company") || undefined,
    page: 1,
    page_size: 6,
  }));
  const [detail, setDetail] = useState<Job | undefined>();
  const { data, isLoading } = useQuery({ queryKey: ["jobs", filters], queryFn: () => getJobs(filters) });
  const { data: syncStatus } = useQuery({ queryKey: ["job-sync-status"], queryFn: getJobSyncStatus });
  const sync = useMutation({
    mutationFn: () => syncJobs({ ...filters, limit: filters.page_size ?? 6 }),
    onSuccess: (result) => {
      message.success(`同步完成：新增 ${result.report?.inserted_count ?? 0} 条`);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["job-sync-status"] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "岗位同步失败"),
  });
  const apply = useMutation({
    mutationFn: (job: Job) => createApplication({ company: job.company, position: job.title, channel: "Web UI", status: "已投递", job_id: job.id, tags: ["匹配"] }),
    onSuccess: () => {
      message.success("已加入投递看板");
      queryClient.invalidateQueries({ queryKey: ["applications"] });
    },
  });
  return (
    <div className="page">
      <PageHeader title="岗位搜索" description="搜索、筛选、分页、匹配度与快速投递。" />
      <div className="toolbar">
        <Space wrap>
          <Typography.Text type="secondary">
            {syncStatus?.provider_configured
              ? `受信任来源：${syncStatus.provider_name}`
              : "当前使用本地岗位快照"}
          </Typography.Text>
          {syncStatus?.account_safe_mode ? <Tag color="green">账号安全模式</Tag> : <Tag color="red">同步已锁定</Tag>}
        </Space>
        <Button
          icon={<RefreshCw size={16} />}
          loading={sync.isPending}
          disabled={!syncStatus?.provider_configured || !syncStatus.sync_enabled || !syncStatus.account_safe_mode}
          onClick={() => sync.mutate()}
        >
          同步岗位
        </Button>
      </div>
      {!syncStatus?.provider_configured ? (
        <Alert
          type="info"
          showIcon
          message="当前页面已可使用本地岗位库"
          description="外部同步需要管理员登记 HTTPS Jobs MCP、来源域名和只读工具白名单；未配置时不会访问招聘网站。"
        />
      ) : null}
      <div className="content-grid">
        <JobFilter onChange={(next) => setFilters({ ...next, page: 1, page_size: 6 })} />
        <div>
          <List
            loading={isLoading}
            grid={{ gutter: 12, xs: 1, sm: 1, md: 2, xl: 3 }}
            dataSource={data?.items ?? []}
            renderItem={(job) => (
              <List.Item>
                <JobCard job={job} onApply={(item) => apply.mutate(item)} onDetail={setDetail} />
              </List.Item>
            )}
          />
          <Pagination
            current={filters.page ?? 1}
            pageSize={filters.page_size ?? 6}
            total={data?.total ?? 0}
            onChange={(page) => setFilters({ ...filters, page })}
          />
        </div>
      </div>
      <JobDetail job={detail} open={Boolean(detail)} onClose={() => setDetail(undefined)} />
    </div>
  );
}
