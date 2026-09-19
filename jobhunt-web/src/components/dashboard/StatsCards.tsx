import { Card, Col, Row, Statistic } from "antd";
import type { ApplicationStats } from "../../types";

export default function StatsCards({ stats }: { stats?: ApplicationStats }) {
  return (
    <Row gutter={[12, 12]}>
      <Col xs={12} lg={6}><Card><Statistic title="总投递数" value={stats?.total ?? 0} /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="面试率" value={stats?.interview_rate ?? 0} suffix="%" /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="Offer 率" value={stats?.offer_rate ?? 0} suffix="%" /></Card></Col>
      <Col xs={12} lg={6}><Card><Statistic title="本周新增" value={stats?.this_week ?? 0} /></Card></Col>
    </Row>
  );
}
