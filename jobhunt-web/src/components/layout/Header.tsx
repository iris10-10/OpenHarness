import { Button, Layout, Space, Typography } from "antd";
import { PanelLeftClose, PanelLeftOpen, RotateCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useSettingsStore } from "../../store/useSettingsStore";

export default function Header() {
  const queryClient = useQueryClient();
  const collapsed = useSettingsStore((state) => state.collapsed);
  const setCollapsed = useSettingsStore((state) => state.setCollapsed);
  return (
    <Layout.Header style={{ background: "#fff", padding: "0 18px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <Space>
        <Button type="text" icon={collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />} onClick={() => setCollapsed(!collapsed)} />
        <Typography.Text strong>求职仪表盘</Typography.Text>
      </Space>
      <Button icon={<RotateCw size={16} />} onClick={() => queryClient.invalidateQueries()}>
        刷新
      </Button>
    </Layout.Header>
  );
}
