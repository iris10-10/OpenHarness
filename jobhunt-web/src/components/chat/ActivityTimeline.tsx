import { Clock3, Wrench } from "lucide-react";
import { Empty, List, Tag, Typography } from "antd";
import type { AgentActivity } from "../../types";

export default function ActivityTimeline({ activities }: { activities: AgentActivity[] }) {
  return (
    <div className="agent-activity-pane">
      <div className="agent-pane-heading">
        <Typography.Text strong>活动</Typography.Text>
        <Clock3 size={16} color="#7a8581" />
      </div>
      {activities.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待工具活动" />
      ) : (
        <List
          size="small"
          dataSource={[...activities].reverse()}
          renderItem={(activity) => (
            <List.Item>
              <List.Item.Meta
                avatar={<Wrench size={15} color={activity.isError ? "#c2413b" : "#2f7d67"} />}
                title={<Typography.Text ellipsis>{activity.toolName}</Typography.Text>}
                description={
                  <Typography.Text type="secondary" ellipsis>
                    {activity.output || (activity.status === "running" ? "正在执行" : "已完成")}
                  </Typography.Text>
                }
              />
              <Tag color={activity.isError ? "error" : activity.status === "running" ? "processing" : "success"}>
                {activity.isError ? "错误" : activity.status === "running" ? "运行中" : "完成"}
              </Tag>
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
