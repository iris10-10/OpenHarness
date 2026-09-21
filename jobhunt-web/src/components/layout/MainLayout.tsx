import { Layout } from "antd";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import Header from "./Header";
import { useSettingsStore } from "../../store/useSettingsStore";

const { Content } = Layout;

export default function MainLayout() {
  const collapsed = useSettingsStore((state) => state.collapsed);
  return (
    <Layout className="app-layout">
      <Sidebar collapsed={collapsed} />
      <Layout>
        <Header />
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
