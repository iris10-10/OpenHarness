import { Card, Col, Row } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getApplicationStats } from "../api/applications";
import PageHeader from "../components/common/PageHeader";
import PieChart from "../components/dashboard/PieChart";
import StatsCards from "../components/dashboard/StatsCards";
import TrendChart from "../components/dashboard/TrendChart";

export default function DashboardPage() {
  const { data } = useQuery({ queryKey: ["application-stats"], queryFn: getApplicationStats });
  return (
    <div className="page">
      <PageHeader title="数据统计" description="投递转化、趋势、状态分布和快捷诊断。" />
      <StatsCards stats={data?.summary} />
      <Row gutter={[12, 12]}>
        <Col xs={24} lg={14}><Card title="投递趋势"><TrendChart data={data?.trend ?? []} /></Card></Col>
        <Col xs={24} lg={10}><Card title="状态分布"><PieChart data={data?.summary.by_status ?? {}} /></Card></Col>
      </Row>
    </div>
  );
}
