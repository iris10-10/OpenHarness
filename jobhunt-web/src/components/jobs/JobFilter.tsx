import { Button, Card, Form, Input, InputNumber, Select, Space } from "antd";
import type { JobFilters } from "../../api/jobs";

export default function JobFilter({ onChange }: { onChange: (filters: JobFilters) => void }) {
  const [form] = Form.useForm<JobFilters>();
  return (
    <Card className="dense-card">
      <Form form={form} layout="vertical" onFinish={(values) => onChange(values)}>
        <Form.Item name="query" label="关键词">
          <Input placeholder="React / 后端 / 公司名" allowClear />
        </Form.Item>
        <Form.Item name="city" label="城市">
          <Select allowClear options={["上海", "杭州", "北京", "深圳", "远程"].map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item name="direction" label="方向">
          <Select allowClear options={["前端", "后端", "算法", "数据", "产品", "测试"].map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item name="company_type" label="公司类型">
          <Select allowClear options={["AI 初创", "成长型", "大厂", "外企"].map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item name="salary_min" label="最低薪资">
          <InputNumber min={0} addonAfter="K" style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="salary_max" label="最高薪资">
          <InputNumber min={0} addonAfter="K" style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item name="experience" label="经验">
          <Select allowClear options={["经验不限", "1-3年", "3-5年", "5-10年"].map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item name="education" label="学历">
          <Select allowClear options={["不限", "大专", "本科", "硕士"].map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit">筛选</Button>
          <Button onClick={() => { form.resetFields(); onChange({}); }}>重置</Button>
        </Space>
      </Form>
    </Card>
  );
}
