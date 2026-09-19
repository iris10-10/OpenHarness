import { Button, Card, Form, Input, List, Select } from "antd";
import { useMutation } from "@tanstack/react-query";
import { createInterviewPractice } from "../api/interview";
import PageHeader from "../components/common/PageHeader";

export default function InterviewPage() {
  const practice = useMutation({ mutationFn: createInterviewPractice });
  const questions = ((practice.data?.questions as Array<Record<string, string>>) ?? []);
  return (
    <div className="page">
      <PageHeader title="面试准备" description="按公司、岗位、轮次生成题目和答题提示。" />
      <Card>
        <Form layout="inline" onFinish={(values) => practice.mutate(values)}>
          <Form.Item name="company"><Input placeholder="公司" /></Form.Item>
          <Form.Item name="position"><Input placeholder="岗位" /></Form.Item>
          <Form.Item name="round" initialValue="技术"><Select style={{ width: 120 }} options={["技术", "行为", "HR"].map((value) => ({ value, label: value }))} /></Form.Item>
          <Button type="primary" htmlType="submit">生成</Button>
        </Form>
      </Card>
      <List dataSource={questions} renderItem={(item, index) => <List.Item><Card style={{ width: "100%" }} title={`题目 ${index + 1}`}>{item.question ?? JSON.stringify(item)}</Card></List.Item>} />
    </div>
  );
}
