import { Button, Form, Input, Modal, Select } from "antd";
import type { ApplicationRecord } from "../../types";
import { APPLICATION_STATUSES } from "../../utils/constants";

export default function ApplicationForm({
  open,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  onCancel: () => void;
  onSubmit: (values: Partial<ApplicationRecord>) => void;
}) {
  const [form] = Form.useForm();
  return (
    <Modal open={open} title="新增投递" onCancel={onCancel} footer={null}>
      <Form form={form} layout="vertical" onFinish={(values) => { onSubmit(values); form.resetFields(); }}>
        <Form.Item name="company" label="公司" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="position" label="岗位" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="channel" label="渠道" initialValue="官网">
          <Input />
        </Form.Item>
        <Form.Item name="status" label="状态" initialValue="已投递">
          <Select options={APPLICATION_STATUSES.map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item name="notes" label="备注">
          <Input.TextArea />
        </Form.Item>
        <Button type="primary" htmlType="submit">保存</Button>
      </Form>
    </Modal>
  );
}
