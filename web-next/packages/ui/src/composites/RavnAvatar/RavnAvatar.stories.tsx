import type { Meta, StoryObj } from '@storybook/react';
import { RavnAvatar } from './RavnAvatar';
import type { PersonaRole } from '@niuulabs/domain';
import type { DotState } from '../../primitives/StateDot';

const meta: Meta<typeof RavnAvatar> = {
  title: 'Composites/RavnAvatar',
  component: RavnAvatar,
  args: { rune: 'ᚱ', state: 'idle', size: 32 },
};
export default meta;

type Story = StoryObj<typeof RavnAvatar>;

export const Idle: Story = { args: { role: 'plan', state: 'idle' } };
export const Running: Story = { args: { role: 'build', state: 'running', pulse: true } };
export const Healthy: Story = { args: { role: 'verify', state: 'healthy' } };
export const Failed: Story = { args: { role: 'gate', state: 'failed' } };
export const Observing: Story = { args: { role: 'audit', state: 'observing' } };

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

const ALL_STATES: DotState[] = [
  'healthy',
  'running',
  'idle',
  'failed',
  'attention',
  'observing',
  'queued',
];

export const AllRoles: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      {ALL_ROLES.map((role) => (
        <div
          key={role}
          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}
        >
          <RavnAvatar role={role} rune="ᚱ" state="idle" size={32} />
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

export const AllStates: Story = {
  render: () => (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      {ALL_STATES.map((state) => (
        <div
          key={state}
          style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}
        >
          <RavnAvatar role="plan" rune="ᚱ" state={state} size={32} />
          <span
            style={{
              fontSize: 10,
              color: 'var(--color-text-muted)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {state}
          </span>
        </div>
      ))}
    </div>
  ),
};

export const MimirRune: Story = {
  args: { role: 'index', rune: 'ᛗ', state: 'observing' },
};
