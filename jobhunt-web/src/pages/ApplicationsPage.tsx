import { Button } from "antd";
import { Plus } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createApplication, deleteApplication, getApplications, updateApplicationStatus } from "../api/applications";
import ApplicationForm from "../components/applications/ApplicationForm";
import KanbanBoard from "../components/applications/KanbanBoard";
import PageHeader from "../components/common/PageHeader";
import StatsCards from "../components/dashboard/StatsCards";
import type { ApplicationRecord, ApplicationStatus } from "../types";

export default function ApplicationsPage() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data } = useQuery({ queryKey: ["applications"], queryFn: getApplications });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["applications"] });
  const create = useMutation({ mutationFn: createApplication, onSuccess: invalidate });
  const move = useMutation({ mutationFn: ({ id, status }: { id: string; status: ApplicationStatus }) => updateApplicationStatus(id, status), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: deleteApplication, onSuccess: invalidate });
  return (
    <div className="page">
      <div className="toolbar">
        <PageHeader title="投递看板" description="新增、状态流转、统计与删除，数据写入本地 JSON。" />
        <Button type="primary" icon={<Plus size={16} />} onClick={() => setOpen(true)}>新增投递</Button>
      </div>
      <StatsCards stats={data?.stats} />
      <KanbanBoard records={(data?.items ?? []) as ApplicationRecord[]} onMove={(id, status) => move.mutate({ id, status })} onDelete={(id) => remove.mutate(id)} />
      <ApplicationForm open={open} onCancel={() => setOpen(false)} onSubmit={(values) => { create.mutate(values); setOpen(false); }} />
    </div>
  );
}
