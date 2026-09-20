import { Button, Empty, Input, List, Space, Tag, Typography } from "antd";
import { MessageSquarePlus, Trash2 } from "lucide-react";
import type { AgentSession } from "../../types";

interface Props {
  sessions: AgentSession[];
  activeId: string | null;
  disabled?: boolean;
  onCreate: () => void;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}

export default function SessionList({ sessions, activeId, disabled, onCreate, onSelect, onDelete }: Props) {
  return (
    <div className="agent-session-pane">
      <div className="agent-pane-heading">
        <Typography.Text strong>会话</Typography.Text>
        <Button
          type="text"
          icon={<MessageSquarePlus size={16} />}
          aria-label="新建会话"
          title="新建会话"
          disabled={disabled}
          onClick={onCreate}
        />
      </div>
      <Input.Search placeholder="搜索会话" allowClear className="agent-session-search" />
      {sessions.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无会话" />
      ) : (
        <List
          className="agent-session-list"
          dataSource={sessions}
          renderItem={(session) => (
            <List.Item
              className={session.session_id === activeId ? "agent-session-item active" : "agent-session-item"}
              onClick={() => onSelect(session.session_id)}
              actions={[
                <Button
                  key="delete"
                  type="text"
                  danger
                  size="small"
                  icon={<Trash2 size={14} />}
                  aria-label={`删除 ${session.title}`}
                  title="删除会话"
                  disabled={disabled || session.busy}
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(session.session_id);
                  }}
                />,
              ]}
            >
              <Space direction="vertical" size={2} style={{ minWidth: 0 }}>
                <Typography.Text ellipsis strong={session.session_id === activeId}>
                  {session.title || "新建会话"}
                </Typography.Text>
                <Typography.Text type="secondary" ellipsis className="agent-session-meta">
                  {session.message_count} 条消息
                </Typography.Text>
              </Space>
              {session.busy ? <Tag color="processing">运行中</Tag> : null}
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
