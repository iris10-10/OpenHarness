import { Button, Card, Descriptions, List, Space, Tag, Upload } from "antd";
import { UploadCloud } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getResumes, uploadResume } from "../api/resumes";
import PageHeader from "../components/common/PageHeader";

export default function ResumesPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["resumes"], queryFn: getResumes });
  const upload = useMutation({ mutationFn: uploadResume, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["resumes"] }) });
  return (
    <div className="page">
      <div className="toolbar">
        <PageHeader title="简历管理" description="上传 PDF/文本简历，解析结构化字段并展示 ATS 分数。" />
        <Upload showUploadList={false} beforeUpload={(file) => { upload.mutate(file); return false; }} accept=".pdf,.txt,.md">
          <Button type="primary" icon={<UploadCloud size={16} />}>上传简历</Button>
        </Upload>
      </div>
      <List
        grid={{ gutter: 12, xs: 1, lg: 2 }}
        dataSource={data?.items ?? []}
        renderItem={(resume) => (
          <List.Item>
            <Card title={resume.name} extra={<Tag color="green">ATS {resume.ats.total}</Tag>}>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="来源">{resume.source}</Descriptions.Item>
                <Descriptions.Item label="技能">
                  <Space wrap>{resume.resume.skills.technical.slice(0, 8).map((skill) => <Tag key={skill}>{skill}</Tag>)}</Space>
                </Descriptions.Item>
                <Descriptions.Item label="经历">{resume.resume.experience.length} 段</Descriptions.Item>
              </Descriptions>
            </Card>
          </List.Item>
        )}
      />
    </div>
  );
}
