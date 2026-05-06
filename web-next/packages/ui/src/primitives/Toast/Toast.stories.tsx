import type { Meta, StoryObj } from '@storybook/react';
import { ToastProvider, useToast } from './Toast';
import type { ToastTone } from './Toast';

const meta: Meta<typeof ToastProvider> = {
  title: 'Overlays/Toast',
  component: ToastProvider,
  parameters: { a11y: {} },
  decorators: [(Story) => <ToastProvider>{Story()}</ToastProvider>],
};
export default meta;

type Story = StoryObj<typeof ToastProvider>;

function ToastDemo({
  title,
  description,
  tone,
}: {
  title: string;
  description?: string;
  tone?: ToastTone;
}) {
  const { toast } = useToast();
  return (
    <button onClick={() => toast({ title, description, tone })}>
      Show {tone ?? 'default'} toast
    </button>
  );
}

export const Default: Story = {
  render: () => <ToastDemo title="Action complete" description="Your item was saved." />,
};

export const Success: Story = {
  render: () => <ToastDemo title="Deployment succeeded" tone="success" />,
};

export const Critical: Story = {
  render: () => (
    <ToastDemo
      title="Deployment failed"
      description="Check the logs for details."
      tone="critical"
    />
  ),
};

export const Warning: Story = {
  render: () => (
    <ToastDemo title="Rate limit approaching" description="80% of quota used." tone="warning" />
  ),
};

export const AllTones: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
      {(['default', 'success', 'critical', 'warning'] as const).map((tone) => (
        <ToastDemo key={tone} title={`${tone} toast`} tone={tone} />
      ))}
    </div>
  ),
};
