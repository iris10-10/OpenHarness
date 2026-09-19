import { Card, List, Tag, Typography } from "antd";
import PageHeader from "../components/common/PageHeader";

const companies = [
  { name: "星河智能", type: "AI 初创", stack: ["React", "Python", "大模型"] },
  { name: "云帆科技", type: "成长型", stack: ["FastAPI", "Redis", "MySQL"] },
];

export default function CompaniesPage() {
  return (
    <div className="page">
      <PageHeader title="公司库" description="公司信息、技术栈和面经线索。" />
      <List
        grid={{ gutter: 12, xs: 1, md: 2 }}
        dataSource={companies}
        renderItem={(company) => (
          <List.Item>
            <Card title={company.name} extra={<Typography.Text type="secondary">{company.type}</Typography.Text>}>
              {company.stack.map((item) => <Tag key={item}>{item}</Tag>)}
            </Card>
          </List.Item>
        )}
      />
    </div>
  );
}
