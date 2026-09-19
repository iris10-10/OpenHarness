import { Button, Card, Form, Input, InputNumber, Space } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getProfile, updateProfile } from "../api/profile";
import PageHeader from "../components/common/PageHeader";

export default function SettingsPage({ setup = false }: { setup?: boolean }) {
  const { data } = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const save = useMutation({ mutationFn: updateProfile });
  return (
    <div className="page">
      <PageHeader title={setup ? "初始化配置" : "用户设置"} description="维护画像、偏好城市、目标薪资和核心技能。" />
      <Card>
        <Form
          layout="vertical"
          initialValues={{
            current_title: (data?.profile?.basic as Record<string, unknown> | undefined)?.current_title,
          }}
          onFinish={(values) =>
            save.mutate({
              basic: { current_title: values.current_title, target_cities: values.cities?.split(/[，,]/).filter(Boolean), years_of_experience: values.years },
              preferences: { expected_salary_min: values.salary_min, expected_salary_max: values.salary_max },
              skills: { technical: values.skills?.split(/[，,]/).filter(Boolean) },
            })
          }
        >
          <Form.Item name="current_title" label="当前方向"><Input placeholder="前端工程师" /></Form.Item>
          <Form.Item name="cities" label="目标城市"><Input placeholder="上海,杭州" /></Form.Item>
          <Form.Item name="skills" label="核心技能"><Input placeholder="React,TypeScript,FastAPI" /></Form.Item>
          <Space>
            <Form.Item name="salary_min" label="最低薪资"><InputNumber addonAfter="K" /></Form.Item>
            <Form.Item name="salary_max" label="最高薪资"><InputNumber addonAfter="K" /></Form.Item>
            <Form.Item name="years" label="经验年限"><InputNumber min={0} /></Form.Item>
          </Space>
          <Button type="primary" htmlType="submit">保存</Button>
        </Form>
      </Card>
    </div>
  );
}
