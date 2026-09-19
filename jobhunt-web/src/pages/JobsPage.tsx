import { App, List, Pagination } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createApplication } from "../api/applications";
import { getJobs, type JobFilters } from "../api/jobs";
import PageHeader from "../components/common/PageHeader";
import JobCard from "../components/jobs/JobCard";
import JobDetail from "../components/jobs/JobDetail";
import JobFilter from "../components/jobs/JobFilter";
import type { Job } from "../types";

export default function JobsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<JobFilters>({ page: 1, page_size: 6 });
  const [detail, setDetail] = useState<Job | undefined>();
  const { data, isLoading } = useQuery({ queryKey: ["jobs", filters], queryFn: () => getJobs(filters) });
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
