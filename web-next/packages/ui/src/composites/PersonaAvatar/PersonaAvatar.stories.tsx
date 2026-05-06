import type { Meta, StoryObj } from '@storybook/react';
import { PersonaAvatar } from './PersonaAvatar';
import type { PersonaRole } from '@niuulabs/domain';

const meta: Meta<typeof PersonaAvatar> = {
  title: 'Composites/PersonaAvatar',
  component: PersonaAvatar,
  args: { letter: 'P', size: 32 },
};
export default meta;

type Story = StoryObj<typeof PersonaAvatar>;

export const Plan: Story = { args: { role: 'plan', letter: 'P' } };
export const Build: Story = { args: { role: 'build', letter: 'B' } };
export const Verify: Story = { args: { role: 'verify', letter: 'V' } };
export const Review: Story = { args: { role: 'review', letter: 'R' } };
export const Gate: Story = { args: { role: 'gate', letter: 'G' } };
export const Audit: Story = { args: { role: 'audit', letter: 'A' } };
export const Ship: Story = { args: { role: 'ship', letter: 'S' } };
export const Index: Story = { args: { role: 'index', letter: 'I' } };
export const Report: Story = { args: { role: 'report', letter: 'R' } };

const ALL_ROLES: PersonaRole[] = [
  'plan',
  'build',
  'verify',
  'review',
  'gate',
  'audit',
  'ship',
  'index',
  'report',
];

export const AllRoles: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      {ALL_ROLES.map((role) => (
        <div
          key={role}
          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}
        >
          <PersonaAvatar role={role} letter={role[0].toUpperCase()} size={32} />
          <span
            style={{
              fontSize: 10,
              color: 'var(--color-text-muted)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {role}
          </span>
        </div>
      ))}
    </div>
  ),
};

export const Sizes: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
      {[16, 22, 28, 36, 48].map((size) => (
        <PersonaAvatar key={size} role="plan" letter="P" size={size} />
      ))}
    </div>
  ),
};
