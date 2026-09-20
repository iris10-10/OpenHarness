import { CaretRightOutlined, CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from "@ant-design/icons";
import { Collapse, Space, Tag, Typography } from "antd";
import type { ChatTool } from "../../types";

export default function ToolCallCard({ tool }: { tool: ChatTool }) {
  const isError = tool.status === "error" || tool.is_error;
  const icon = isError ? <CloseCircleOutlined /> : tool.status === "completed" ? <CheckCircleOutlined /> : <LoadingOutlined />;
  return (
    <Collapse
      className="tool-call-card"
      ghost
      expandIcon={({ isActive }) => <CaretRightOutlined rotate={isActive ? 90 : 0} />}
      items={[
        {
          key: tool.name,
          label: (
            <Space size={8}>
              {icon}
              <Typography.Text>{tool.name}</Typography.Text>
              <Tag color={isError ? "error" : tool.status === "completed" ? "success" : "processing"}>
                {isError ? "失败" : tool.status === "completed" ? "完成" : "运行中"}
              </Tag>
            </Space>
          ),
          children: (
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {tool.input ? <pre className="agent-code-output">{JSON.stringify(tool.input, null, 2)}</pre> : null}
              {tool.output ? <pre className="agent-code-output">{tool.output}</pre> : null}
            </Space>
          ),
        },
      ]}
    />
  );
}
