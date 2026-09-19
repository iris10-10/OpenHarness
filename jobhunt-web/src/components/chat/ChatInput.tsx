import { Button, Input, Space } from "antd";
import { Send } from "lucide-react";
import { useState } from "react";

export default function ChatInput({ disabled, onSend }: { disabled?: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  return (
    <Space.Compact style={{ width: "100%" }}>
      <Input.TextArea
        value={text}
        autoSize={{ minRows: 1, maxRows: 5 }}
        placeholder="输入求职问题，或使用 /search、/match 等快捷指令"
        onChange={(event) => setText(event.target.value)}
        onPressEnter={(event) => {
          if (!event.shiftKey) {
            event.preventDefault();
            if (text.trim()) {
              onSend(text);
              setText("");
            }
          }
        }}
      />
      <Button
        type="primary"
        icon={<Send size={16} />}
        disabled={disabled || !text.trim()}
        onClick={() => {
          onSend(text);
          setText("");
        }}
      />
    </Space.Compact>
  );
}
