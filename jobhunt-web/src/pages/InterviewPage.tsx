import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, History, Play, Send, Square } from "lucide-react";
import { useEffect, useState } from "react";
import {
  createInterviewPractice,
  finishInterview,
  getInterviewSession,
  getInterviewSessions,
  submitInterviewAnswer,
  type InterviewEvaluation,
  type InterviewPracticeResponse,
  type InterviewSession,
} from "../api/interview";
import PageHeader from "../components/common/PageHeader";

const { Paragraph, Text, Title } = Typography;

type PracticeFormValues = {
  company: string;
  position: string;
  round: string;
  difficulty: string;
  count: number;
  jd_text: string;
  resume_text: string;
};

const INITIAL_VALUES: PracticeFormValues = {
  company: "",
  position: "",
  round: "技术",
  difficulty: "混合",
  count: 5,
  jd_text: "",
  resume_text: "",
};

function statusColor(status: string) {
  return status === "进行中" ? "processing" : "success";
}

function scoreColor(score: number) {
  if (score >= 85) return "#18794e";
  if (score >= 70) return "#9a6700";
  return "#b42318";
}

function FeedbackPanel({ feedback }: { feedback: InterviewEvaluation }) {
  return (
    <Card
      size="small"
      className="interview-feedback"
      title="本题反馈"
      extra={
        <Space>
          <Text>得分</Text>
          <Text strong style={{ color: scoreColor(feedback.score), fontSize: 20 }}>
            {feedback.score}
          </Text>
          <Tag color={feedback.grade === "优秀" ? "green" : "gold"}>{feedback.grade}</Tag>
        </Space>
      }
    >
      <Row gutter={[16, 12]}>
        {(feedback.dimensions ?? []).map((dimension) => (
          <Col xs={24} md={12} key={dimension.name}>
            <div className="interview-dimension">
              <div className="interview-dimension-heading">
                <Text>{dimension.name}</Text>
                <Text strong>
                  {dimension.score}/{dimension.max_score}
                </Text>
              </div>
              <Progress
                percent={Math.round((dimension.score / dimension.max_score) * 100)}
                showInfo={false}
                strokeColor={scoreColor(dimension.score / dimension.max_score * 100)}
              />
              <Text type="secondary">{dimension.advice}</Text>
            </div>
          </Col>
        ))}
      </Row>
      {feedback.missing_keywords?.length ? (
        <Alert
          className="interview-feedback-alert"
          type="warning"
          showIcon
          message={`建议补充：${feedback.missing_keywords.join("、")}`}
        />
      ) : null}
      {feedback.suggestions?.length ? (
        <List
          size="small"
          header="下一次回答重点"
          dataSource={feedback.suggestions}
          renderItem={(item) => <List.Item>{item}</List.Item>}
        />
      ) : null}
    </Card>
  );
}

function ReportPanel({ session }: { session: InterviewSession }) {
  const evaluations = session.evaluations ?? [];
  return (
    <Card
      title="模拟面试报告"
      extra={<Tag color={statusColor(session.status)}>{session.status}</Tag>}
    >
      <Row gutter={[12, 12]} className="interview-report-stats">
        <Col xs={8}>
          <Statistic title="平均分" value={session.summary.score} suffix="/100" />
        </Col>
        <Col xs={8}>
          <Statistic title="完成度" value={session.summary.completion_rate} suffix="%" />
        </Col>
        <Col xs={8}>
          <Statistic title="等级" value={session.summary.grade} />
        </Col>
      </Row>
      {session.finish_reason ? (
        <Alert type="info" showIcon message={`结束原因：${session.finish_reason}`} />
      ) : null}
      <Collapse
        className="interview-review-list"
        items={evaluations.map((evaluation, index) => ({
          key: String(index),
          label: `第 ${(evaluation.question_index ?? index) + 1} 题 · ${evaluation.score} 分 · ${evaluation.grade}`,
          children: (
            <Space direction="vertical" size={10} style={{ width: "100%" }}>
              <Text strong>{evaluation.question}</Text>
              {evaluation.answer ? (
                <Paragraph className="interview-answer-preview">{evaluation.answer}</Paragraph>
              ) : null}
              <FeedbackPanel feedback={evaluation} />
            </Space>
          ),
        }))}
      />
      {!evaluations.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有完成任何题目" />
      ) : null}
    </Card>
  );
}

export default function InterviewPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm<PracticeFormValues>();
  const [answerForm] = Form.useForm<{ answer: string }>();
  const [activeSessionId, setActiveSessionId] = useState("");
  const [workingSession, setWorkingSession] = useState<InterviewSession | null>(null);
  const [lastFeedback, setLastFeedback] = useState<InterviewEvaluation | null>(null);
  const [guidance, setGuidance] = useState("");

  const history = useQuery({
    queryKey: ["interview-sessions"],
    queryFn: getInterviewSessions,
  });
  const detail = useQuery({
    queryKey: ["interview-session", activeSessionId],
    queryFn: () => getInterviewSession(activeSessionId),
    enabled: Boolean(activeSessionId) && !workingSession,
  });

  const start = useMutation({
    mutationFn: createInterviewPractice,
    onSuccess: (data: InterviewPracticeResponse) => {
      setActiveSessionId(data.session.id);
      setWorkingSession(data.session);
      setLastFeedback(null);
      setGuidance(data.guidance);
      answerForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["interview-sessions"] });
      message.success("面试会话已创建，开始回答第一题");
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "创建会话失败"),
  });

  const answer = useMutation({
    mutationFn: ({
      id,
      value,
      questionIndex,
    }: {
      id: string;
      value: string;
      questionIndex: number;
    }) => submitInterviewAnswer(id, value, questionIndex),
    onSuccess: (data) => {
      setWorkingSession(data.session);
      setLastFeedback(data.feedback);
      answerForm.resetFields();
      queryClient.invalidateQueries({ queryKey: ["interview-sessions"] });
      message.success(data.finished ? "面试已完成，报告已生成" : "评分完成，进入下一题");
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "提交答案失败"),
  });

  const finish = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) => finishInterview(id, reason),
    onSuccess: (data) => {
      setWorkingSession(data.session);
      setLastFeedback(null);
      queryClient.invalidateQueries({ queryKey: ["interview-sessions"] });
      message.success("面试已结束");
    },
    onError: (error) => message.error(error instanceof Error ? error.message : "结束面试失败"),
  });

  const session = workingSession ?? detail.data?.session ?? null;
  const currentQuestion = session?.current_question ?? null;

  useEffect(() => {
    answerForm.resetFields();
  }, [answerForm, session?.current_index]);

  const selectSession = (id: string) => {
    setActiveSessionId(id);
    setWorkingSession(null);
    setLastFeedback(null);
    setGuidance("");
  };

  const startPractice = (values: PracticeFormValues) => {
    start.mutate({
      company: values.company,
      position: values.position,
      round: values.round,
      difficulty: values.difficulty,
      count: values.count,
      jd_text: values.jd_text,
      resume_text: values.resume_text,
    });
  };

  return (
    <div className="page">
      <PageHeader
        title="面试准备"
        description="按目标岗位生成题目，逐题回答并获得可复盘的评分报告。"
      />
      <div className="interview-layout">
        <Card title="开始一次练习" className="interview-config-card">
          <Form
            form={form}
            layout="vertical"
            initialValues={INITIAL_VALUES}
            onFinish={startPractice}
          >
            <Row gutter={12}>
              <Col xs={24} sm={12}>
                <Form.Item name="company" label="目标公司">
                  <Input placeholder="例如：星辰科技" />
                </Form.Item>
              </Col>
              <Col xs={24} sm={12}>
                <Form.Item
                  name="position"
                  label="目标岗位"
                  rules={[{ required: true, message: "请输入目标岗位" }]}
                >
                  <Input placeholder="例如：Python 后端工程师" />
                </Form.Item>
              </Col>
              <Col xs={12} sm={8}>
                <Form.Item name="round" label="面试轮次">
                  <Select
                    options={["技术", "行为", "HR", "高管"].map((value) => ({
                      value,
                      label: value,
                    }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={12} sm={8}>
                <Form.Item name="difficulty" label="难度">
                  <Select
                    options={["基础", "进阶", "困难", "混合"].map((value) => ({
                      value,
                      label: value,
                    }))}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} sm={8}>
                <Form.Item name="count" label="题目数量">
                  <InputNumber min={1} max={20} style={{ width: "100%" }} />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item name="jd_text" label="岗位 JD">
              <Input.TextArea
                rows={5}
                placeholder="粘贴 JD 后，题目会优先覆盖岗位要求的技能和项目场景。"
              />
            </Form.Item>
            <Form.Item name="resume_text" label="简历文本（可选）">
              <Input.TextArea
                rows={4}
                placeholder="粘贴简历后，项目深挖题会更贴合你的经历。"
              />
            </Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              icon={<Play size={16} />}
              loading={start.isPending}
              block
            >
              创建模拟面试
            </Button>
          </Form>
        </Card>

        <div className="interview-session-column">
          {!session && !detail.isLoading ? (
            <Card className="interview-empty-card">
              <Empty
                image={<History size={42} strokeWidth={1.5} />}
                description="创建会话或从右侧历史记录继续复盘"
              />
            </Card>
          ) : null}
          {detail.isLoading ? (
            <Card className="interview-empty-card">
              <Spin />
            </Card>
          ) : null}
          {session ? (
            <>
              <Card
                title={
                  <Space>
                    <span>{session.company || "未指定公司"}</span>
                    <Text type="secondary">{session.position}</Text>
                  </Space>
                }
                extra={<Tag color={statusColor(session.status)}>{session.status}</Tag>}
              >
                <Descriptions size="small" column={{ xs: 1, sm: 3 }}>
                  <Descriptions.Item label="轮次">{session.round}</Descriptions.Item>
                  <Descriptions.Item label="难度">{session.difficulty}</Descriptions.Item>
                  <Descriptions.Item label="进度">
                    {session.summary.answered_count}/{session.summary.question_count}
                  </Descriptions.Item>
                </Descriptions>
                <Progress
                  percent={session.summary.completion_rate}
                  status={session.status === "进行中" ? "active" : "success"}
                  format={(value) => `${value}%`}
                />
                {session.status === "进行中" && currentQuestion ? (
                  <div className="interview-question-area">
                    <Space wrap>
                      <Tag color="blue">第 {session.current_index + 1} 题</Tag>
                      <Tag>{currentQuestion.category}</Tag>
                      <Tag>{currentQuestion.difficulty}</Tag>
                    </Space>
                    <Title level={4}>{currentQuestion.question}</Title>
                    <Form form={answerForm} layout="vertical" onFinish={({ answer: value }) => {
                      answer.mutate({
                        id: session.id,
                        value: value.trim(),
                        questionIndex: session.current_index,
                      });
                    }}>
                      <Form.Item
                        name="answer"
                        rules={[
                          { required: true, message: "请输入回答后再提交" },
                          { min: 10, message: "回答至少写出你的思路和依据" },
                        ]}
                      >
                        <Input.TextArea
                          rows={9}
                          showCount
                          maxLength={20_000}
                          placeholder="建议按“结论 → 原理/方案 → 实践案例 → 权衡”组织回答。"
                        />
                      </Form.Item>
                      <Space wrap>
                        <Button
                          type="primary"
                          htmlType="submit"
                          icon={<Send size={16} />}
                          loading={answer.isPending}
                        >
                          提交回答并评分
                        </Button>
                        <Button
                          danger
                          icon={<Square size={15} />}
                          loading={finish.isPending}
                          onClick={() => finish.mutate({ id: session.id, reason: "用户提前结束" })}
                        >
                          提前结束
                        </Button>
                      </Space>
                    </Form>
                  </div>
                ) : (
                  <ReportPanel session={session} />
                )}
              </Card>
              {lastFeedback && session.status === "进行中" ? (
                <FeedbackPanel feedback={lastFeedback} />
              ) : null}
              {session.status === "进行中" && guidance ? (
                <Alert type="info" showIcon message="本轮答题建议" description={guidance} />
              ) : null}
            </>
          ) : null}
        </div>

        <Card
          title="历史练习"
          className="interview-history-card"
          extra={<Text type="secondary">{history.data?.total ?? 0} 次</Text>}
        >
          {history.isLoading ? (
            <Spin />
          ) : (
            <List
              dataSource={history.data?.items ?? []}
              locale={{ emptyText: "暂无练习记录" }}
              renderItem={(item) => (
                <List.Item
                  className={`interview-history-item ${activeSessionId === item.id ? "active" : ""}`}
                  onClick={() => selectSession(item.id)}
                  actions={[
                    <Tag key="status" color={statusColor(item.status)}>
                      {item.summary.grade}
                    </Tag>,
                  ]}
                >
                  <List.Item.Meta
                    avatar={<CheckCircle2 size={18} color={item.status === "进行中" ? "#2f7d67" : "#8b9b95"} />}
                    title={item.position}
                    description={`${item.company || "未指定公司"} · ${item.round} · ${item.summary.answered_count}/${item.summary.question_count} 题`}
                  />
                </List.Item>
              )}
            />
          )}
        </Card>
      </div>
    </div>
  );
}
