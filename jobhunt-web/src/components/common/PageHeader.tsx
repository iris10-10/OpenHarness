import { Typography } from "antd";

export default function PageHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div>
      <Typography.Title level={3} style={{ margin: 0 }}>
        {title}
      </Typography.Title>
      {description ? <Typography.Text type="secondary">{description}</Typography.Text> : null}
    </div>
  );
}
