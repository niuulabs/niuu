import type { Meta, StoryObj } from '@storybook/react';
import { DeployBadge } from './DeployBadge';
import type { DeploymentKind } from './DeployBadge';

const meta: Meta<typeof DeployBadge> = {
  title: 'Composites/DeployBadge',
  component: DeployBadge,
};
export default meta;

type Story = StoryObj<typeof DeployBadge>;

export const K8s: Story = { args: { deployment: 'k8s' } };
export const Systemd: Story = { args: { deployment: 'systemd' } };
export const Pi: Story = { args: { deployment: 'pi' } };
export const Mobile: Story = { args: { deployment: 'mobile' } };
export const Ephemeral: Story = { args: { deployment: 'ephemeral' } };

const ALL: DeploymentKind[] = ['k8s', 'systemd', 'pi', 'mobile', 'ephemeral'];

export const AllKinds: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {ALL.map((d) => (
        <DeployBadge key={d} deployment={d} />
      ))}
    </div>
  ),
};
