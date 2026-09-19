import { Layout } from "antd";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import Header from "./Header";
import { useSettingsStore } from "../../store/useSettingsStore";

const { Content } = Layout;

export default function MainLayout() {
  const collapsed = useSettingsStore((state) => state.collapsed);
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sidebar collapsed={collapsed} />
      <Layout>
        <Header />
        <Content style={{ padding: 20, minWidth: 0 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
