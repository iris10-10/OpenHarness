import { Input, List, Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { BriefcaseBusiness, ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import { useState } from "react";
import { getCompanies } from "../api/jobs";
import PageHeader from "../components/common/PageHeader";

export default function CompaniesPage() {
  const [query, setQuery] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["companies", query],
    queryFn: () => getCompanies(query),
  });

  return (
    <div className="page">
      <PageHeader title="公司库" description="由岗位同步自动聚合公司、城市、部门和官方招聘来源。" />
      <Input.Search
        allowClear
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索公司或别名"
        style={{ maxWidth: 360 }}
      />
      {!isLoading && !data?.items.length ? (
        <Typography.Text type="secondary">
          还没有公司数据。先在岗位搜索页同步已登记的官方来源。
        </Typography.Text>
      ) : null}
      <List
        loading={isLoading}
        grid={{ gutter: 12, xs: 1, md: 2 }}
        dataSource={data?.items ?? []}
        renderItem={(company) => (
          <List.Item>
            <div className="company-library-item">
              <div className="company-library-heading">
                <div>
                  <Typography.Title level={4}>{company.name}</Typography.Title>
                  <Typography.Text type="secondary">
                    {company.job_count} 个岗位 · 最近同步 {company.last_synced_at?.slice(0, 10) || "-"}
                  </Typography.Text>
                </div>
                <BriefcaseBusiness size={20} aria-hidden="true" />
              </div>
              <Space wrap>
                {company.cities.map((city) => <Tag key={city}>{city}</Tag>)}
                {company.departments.slice(0, 4).map((department) => (
                  <Tag key={department} color="blue">{department}</Tag>
                ))}
              </Space>
              <Space wrap>
                <Link to={`/jobs?company=${encodeURIComponent(company.name)}`}>
                  查看该公司岗位 <BriefcaseBusiness size={14} />
                </Link>
                {company.official_career_url ? (
                  <a href={company.official_career_url} target="_blank" rel="noreferrer">
                    官方招聘页 <ExternalLink size={14} />
                  </a>
                ) : null}
              </Space>
              <Typography.Text type="secondary">
                来源：{company.source_codes.join("、") || "本地岗位库"}
              </Typography.Text>
            </div>
          </List.Item>
        )}
      />
    </div>
  );
}
