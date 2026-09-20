import { Button, Input, Space } from "antd";
import { Send } from "lucide-react";
export default function ChatInput({
  disabled,
  value,
  onChange,
  onSend,
}: {
  disabled?: boolean;
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string) => void;
}) {
  return (
    <Space.Compact style={{ width: "100%" }}>
      <Input.TextArea
        value={value}
        autoSize={{ minRows: 1, maxRows: 5 }}
        placeholder="输入求职问题，或使用 /search、/match 等快捷指令"
        onChange={(event) => onChange(event.target.value)}
        onPressEnter={(event) => {
          if (!event.shiftKey) {
            event.preventDefault();
            if (value.trim()) onSend(value);
          }
        }}
      />
      <Button
        type="primary"
        icon={<Send size={16} />}
        disabled={disabled || !value.trim()}
        onClick={() => {
          if (value.trim()) onSend(value);
        }}
      />
    </Space.Compact>
  );
}
