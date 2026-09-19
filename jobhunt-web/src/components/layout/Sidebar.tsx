import { Layout, Menu, Typography } from "antd";
import { BarChart3, BriefcaseBusiness, Building2, FileText, MessageSquare, Mic, Settings, Columns3 } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { useSettingsStore } from "../../store/useSettingsStore";

const { Sider } = Layout;

const items = [
  { key: "/dashboard", icon: <BarChart3 size={18} />, label: "数据统计" },
  { key: "/chat", icon: <MessageSquare size={18} />, label: "对话助手" },
  { key: "/jobs", icon: <BriefcaseBusiness size={18} />, label: "岗位搜索" },
  { key: "/applications", icon: <Columns3 size={18} />, label: "投递看板" },
  { key: "/resumes", icon: <FileText size={18} />, label: "简历管理" },
  { key: "/interview", icon: <Mic size={18} />, label: "面试准备" },
  { key: "/companies", icon: <Building2 size={18} />, label: "公司库" },
  { key: "/settings", icon: <Settings size={18} />, label: "用户设置" },
];

export default function Sidebar({ collapsed }: { collapsed: boolean }) {
  const navigate = useNavigate();
  const location = useLocation();
  const setCollapsed = useSettingsStore((state) => state.setCollapsed);
  return (
    <Sider
      collapsible
      collapsed={collapsed}
      collapsedWidth={80}
      trigger={null}
      breakpoint="lg"
      width={232}
      onBreakpoint={setCollapsed}
    >
      <div style={{ height: 58, display: "flex", alignItems: "center", padding: "0 18px" }}>
        <Typography.Text style={{ color: "#fff", fontWeight: 700, fontSize: 16 }} ellipsis>
          OpenHarness 求职
        </Typography.Text>
      </div>
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[items.find((item) => location.pathname.startsWith(item.key))?.key ?? "/dashboard"]}
        items={items}
        onClick={({ key }) => navigate(key)}
      />
    </Sider>
  );
}
