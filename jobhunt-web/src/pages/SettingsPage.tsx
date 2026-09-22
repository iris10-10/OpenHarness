import { Alert, App, Button, Card, Checkbox, Col, Divider, Form, Input, InputNumber, Row, Space, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { getProfile, type ProfileData, updateProfile } from "../api/profile";
import PageHeader from "../components/common/PageHeader";

const PROFILE_SECTIONS = [
  { label: "基本信息", value: "basic" },
  { label: "技能", value: "skills" },
  { label: "教育背景", value: "education" },
  { label: "经历", value: "experience" },
  { label: "求职偏好", value: "preferences" },
  { label: "求职状态", value: "job_search_status" },
];

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function joinList(value: unknown): string {
  return asList(value).join("、");
}

function splitList(value: unknown): string[] {
  return String(value ?? "")
    .split(/[，,、\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export default function SettingsPage({ setup = false }: { setup?: boolean }) {
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["profile"], queryFn: getProfile });
  const save = useMutation({
    mutationFn: updateProfile,
    onSuccess: (result) => {
      queryClient.setQueryData(["profile"], result);
      message.success("用户画像已保存");
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "用户画像保存失败");
    },
  });

  const profile = data?.profile ?? {};
  const basic = asRecord(profile.basic);
  const skills = asRecord(profile.skills);
  const education = asRecord(profile.education);
  const experience = asRecord(profile.experience);
  const preferences = asRecord(profile.preferences);
  const privacy = asRecord(profile.privacy);
  const visibility = data?.ai_visibility;

  useEffect(() => {
    if (!data) return;
    form.setFieldsValue({
      display_name: basic.display_name ?? basic.name,
      current_city: basic.current_city,
      target_cities: joinList(basic.target_cities),
      current_title: basic.current_title,
      years_of_experience: basic.years_of_experience,
      current_salary: basic.current_salary,
      phone: basic.phone,
      email: basic.email,
      technical: joinList(skills.technical),
      domains: joinList(skills.domains),
      soft_skills: joinList(skills.soft),
      languages: joinList(skills.languages),
      school: education.school,
      degree: education.degree,
      major: education.major,
      graduation_year: education.graduation_year,
      experience_summary: experience.summary,
      current_company: experience.current_company,
      projects: experience.projects,
      target_positions: joinList(preferences.target_positions),
      expected_salary_min: preferences.expected_salary_min,
      expected_salary_max: preferences.expected_salary_max,
      company_types: joinList(preferences.company_types),
      work_mode: preferences.work_mode,
      accept_travel: preferences.accept_travel,
      accept_overtime: preferences.accept_overtime,
      job_search_status: profile.job_search_status,
      ai_enabled: privacy.ai_enabled ?? true,
      allowed_sections: privacy.allowed_sections ?? PROFILE_SECTIONS.map((item) => item.value),
      include_sensitive_fields: privacy.include_sensitive_fields ?? false,
      include_contact_fields: privacy.include_contact_fields ?? false,
    });
  }, [data]);

  const handleSubmit = (values: Record<string, unknown>) => {
    const nextProfile: ProfileData = {
      basic: {
        display_name: String(values.display_name ?? "").trim(),
        current_city: String(values.current_city ?? "").trim(),
        target_cities: splitList(values.target_cities),
        current_title: String(values.current_title ?? "").trim(),
        years_of_experience: values.years_of_experience ?? null,
        current_salary: values.current_salary ?? null,
        phone: String(values.phone ?? "").trim(),
        email: String(values.email ?? "").trim(),
      },
      skills: {
        technical: splitList(values.technical),
        domains: splitList(values.domains),
        soft: splitList(values.soft_skills),
        languages: splitList(values.languages),
      },
      education: {
        school: String(values.school ?? "").trim(),
        degree: String(values.degree ?? "").trim(),
        major: String(values.major ?? "").trim(),
        graduation_year: values.graduation_year ?? null,
      },
      experience: {
        summary: String(values.experience_summary ?? "").trim(),
        current_company: String(values.current_company ?? "").trim(),
        projects: String(values.projects ?? "").trim(),
      },
      preferences: {
        target_positions: splitList(values.target_positions),
        expected_salary_min: values.expected_salary_min ?? null,
        expected_salary_max: values.expected_salary_max ?? null,
        company_types: splitList(values.company_types),
        work_mode: String(values.work_mode ?? "").trim(),
        accept_travel: Boolean(values.accept_travel),
        accept_overtime: Boolean(values.accept_overtime),
      },
      job_search_status: String(values.job_search_status ?? "").trim(),
      privacy: {
        ai_enabled: Boolean(values.ai_enabled),
        allowed_sections: values.allowed_sections ?? [],
        include_sensitive_fields: Boolean(values.include_sensitive_fields),
        include_contact_fields: Boolean(values.include_contact_fields),
      },
    };
    save.mutate(nextProfile);
  };

  return (
    <div className="page">
      <PageHeader
        title={setup ? "创建用户画像" : "用户画像"}
        description="维护你的经历与求职偏好，并明确控制哪些信息可以被 AI 使用。"
      />
      <Form form={form} layout="vertical" onFinish={handleSubmit} disabled={isLoading}>
        <div className="profile-settings-grid">
          <div>
            <Card title="基本信息" className="profile-section-card">
              <Row gutter={16}>
                <Col xs={24} md={12}><Form.Item name="display_name" label="显示名称"><Input placeholder="例如：小林" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="current_title" label="当前职位或方向"><Input placeholder="例如：后端工程师" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="current_city" label="当前城市"><Input placeholder="例如：杭州" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="target_cities" label="目标城市"><Input placeholder="上海、杭州、深圳" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="years_of_experience" label="工作年限"><InputNumber min={0} step={0.5} style={{ width: "100%" }} /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="current_salary" label="当前月薪（K，可选）"><InputNumber min={0} style={{ width: "100%" }} /></Form.Item></Col>
              </Row>
              <Divider orientation="left">联系方式（默认不提供给 AI）</Divider>
              <Row gutter={16}>
                <Col xs={24} md={12}><Form.Item name="phone" label="手机号"><Input placeholder="仅在你明确允许时使用" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="email" label="邮箱"><Input placeholder="仅在你明确允许时使用" /></Form.Item></Col>
              </Row>
            </Card>

            <Card title="技能与经历" className="profile-section-card">
              <Form.Item name="technical" label="技术技能"><Input.TextArea rows={2} placeholder="Python、FastAPI、React" /></Form.Item>
              <Row gutter={16}>
                <Col xs={24} md={12}><Form.Item name="domains" label="熟悉领域"><Input placeholder="电商、金融、数据平台" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="soft_skills" label="软技能"><Input placeholder="沟通、项目管理" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="languages" label="语言"><Input placeholder="中文、英语" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="current_company" label="当前公司（可选）"><Input /></Form.Item></Col>
              </Row>
              <Form.Item name="experience_summary" label="经历概述"><Input.TextArea rows={3} placeholder="简要描述你的工作经历和优势" /></Form.Item>
              <Form.Item name="projects" label="代表项目"><Input.TextArea rows={3} placeholder="项目名称、职责、结果或技术亮点" /></Form.Item>
            </Card>

            <Card title="教育背景" className="profile-section-card">
              <Row gutter={16}>
                <Col xs={24} md={12}><Form.Item name="school" label="学校"><Input /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="degree" label="学历"><Input placeholder="本科、硕士" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="major" label="专业"><Input /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="graduation_year" label="毕业年份"><InputNumber min={1900} max={2200} style={{ width: "100%" }} /></Form.Item></Col>
              </Row>
            </Card>
          </div>

          <div>
            <Card title="求职偏好" className="profile-section-card">
              <Form.Item name="target_positions" label="目标岗位"><Input.TextArea rows={2} placeholder="后端开发、平台工程师" /></Form.Item>
              <Row gutter={16}>
                <Col xs={24} md={12}><Form.Item name="expected_salary_min" label="期望最低月薪（K）"><InputNumber min={0} style={{ width: "100%" }} /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item name="expected_salary_max" label="期望最高月薪（K）"><InputNumber min={0} style={{ width: "100%" }} /></Form.Item></Col>
                <Col xs={24}><Form.Item name="company_types" label="公司类型"><Input placeholder="互联网、外企、创业公司" /></Form.Item></Col>
                <Col xs={24}><Form.Item name="work_mode" label="工作方式"><Input placeholder="现场、混合、远程" /></Form.Item></Col>
              </Row>
              <Form.Item name="job_search_status" label="求职状态"><Input placeholder="积极找、观望、不找" /></Form.Item>
              <Space direction="vertical">
                <Form.Item name="accept_travel" valuePropName="checked" noStyle><Checkbox>接受出差</Checkbox></Form.Item>
                <Form.Item name="accept_overtime" valuePropName="checked" noStyle><Checkbox>接受加班</Checkbox></Form.Item>
              </Space>
            </Card>

            <Card title="AI 可见范围" className="profile-section-card">
              <Alert
                type="info"
                showIcon
                message="当前画像保存在本地；勾选的信息会随相关对话发送给你配置的模型服务商。"
                description="联系方式和敏感字段默认关闭，关闭“允许 AI 使用画像”后，画像不会自动注入对话上下文。"
              />
              <div style={{ marginTop: 16 }}>
                <Form.Item name="ai_enabled" valuePropName="checked"><Checkbox>允许 AI 自动使用我的用户画像</Checkbox></Form.Item>
                <Form.Item name="allowed_sections" label="允许 AI 使用的分区">
                  <Checkbox.Group options={PROFILE_SECTIONS} />
                </Form.Item>
                <Form.Item name="include_sensitive_fields" valuePropName="checked">
                  <Checkbox>允许使用敏感字段（例如当前薪资、姓名）</Checkbox>
                </Form.Item>
                <Form.Item name="include_contact_fields" valuePropName="checked">
                  <Checkbox>允许使用联系方式（手机号、邮箱、地址）</Checkbox>
                </Form.Item>
              </div>
              {visibility ? (
                <div className="profile-visibility-preview">
                  <Typography.Text strong>当前 AI 可见内容</Typography.Text>
                  <div style={{ marginTop: 8 }}>
                    {visibility.enabled && visibility.included_sections.length > 0
                      ? visibility.included_sections.map((section) => <Tag color="green" key={section}>{PROFILE_SECTIONS.find((item) => item.value === section)?.label ?? section}</Tag>)
                      : <Typography.Text type="secondary">没有画像信息会自动发送</Typography.Text>}
                  </div>
                  <Typography.Text type="secondary" style={{ display: "block", marginTop: 8 }}>{visibility.reason}</Typography.Text>
                </div>
              ) : null}
            </Card>
          </div>
        </div>
        <div className="profile-save-bar">
          <Typography.Text type="secondary">保存后会立即用于后续对话和岗位匹配。</Typography.Text>
          <Button type="primary" htmlType="submit" loading={save.isPending}>保存用户画像</Button>
        </div>
      </Form>
    </div>
  );
}
