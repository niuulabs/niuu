import type { Meta, StoryObj } from '@storybook/react';
import { MountChip } from './MountChip';
import type { MountChipRole } from './MountChip';

const meta: Meta<typeof MountChip> = {
  title: 'Composites/MountChip',
  component: MountChip,
  args: { name: 'volundr-local' },
};
export default meta;

type Story = StoryObj<typeof MountChip>;

// Binding roles
export const Primary: Story = { args: { role: 'primary', priority: 1 } };
export const Archive: Story = { args: { role: 'archive', priority: 2 } };
export const ReadOnly: Story = { args: { role: 'ro' } };

// Mount-kind roles
export const Local: Story = { args: { name: 'local-well', role: 'local' } };
export const Shared: Story = { args: { name: 'realm-well', role: 'shared' } };
export const Domain: Story = { args: { name: 'code-well', role: 'domain' } };

const BINDING_ROLES: MountChipRole[] = ['primary', 'archive', 'ro'];
const KIND_ROLES: MountChipRole[] = ['local', 'shared', 'domain'];

export const AllBindingRoles: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {BINDING_ROLES.map((role) => (
        <MountChip key={role} name="volundr-local" role={role} priority={1} />
      ))}
    </div>
  ),
};

export const AllKindRoles: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {KIND_ROLES.map((role) => (
        <MountChip key={role} name="knowledge-well" role={role} />
      ))}
    </div>
  ),
};

export const PriorityOrdered: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      <MountChip name="primary-mount" role="primary" priority={1} />
      <MountChip name="archive-mount" role="archive" priority={2} />
      <MountChip name="readonly-mount" role="ro" />
    </div>
  ),
};
