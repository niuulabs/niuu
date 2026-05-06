import type { Meta, StoryObj } from '@storybook/react';
import { MeshEventCard } from './MeshEventCard';
import type { MeshEvent } from '../../types';

const meta = {
  title: 'Chat/MeshEventCard',
  component: MeshEventCard,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof MeshEventCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Outcome: Story = {
  args: {
    event: {
      id: 'e1',
      type: 'outcome',
      participantId: 'peer-1',
      participant: { color: '#38bdf8' },
      timestamp: new Date(),
      persona: 'Ada',
      eventType: 'review',
      verdict: 'pass',
      summary: 'All checks passed',
    } satisfies MeshEvent,
  },
};

export const OutcomeFailed: Story = {
  args: {
    event: {
      id: 'e2',
      type: 'outcome',
      participantId: 'peer-1',
      participant: { color: '#f87171' },
      timestamp: new Date(),
      persona: 'Björk',
      eventType: 'lint',
      verdict: 'fail',
      summary: 'Lint errors found',
    } satisfies MeshEvent,
  },
};

export const Delegation: Story = {
  args: {
    event: {
      id: 'e3',
      type: 'mesh_message',
      participantId: 'peer-1',
      participant: { color: '#38bdf8' },
      timestamp: new Date(),
      fromPersona: 'Ada',
      eventType: 'delegate',
      preview: 'Please review this PR',
    } satisfies MeshEvent,
  },
};

export const Notification: Story = {
  args: {
    event: {
      id: 'e4',
      type: 'notification',
      participantId: 'peer-2',
      participant: { color: '#a78bfa' },
      timestamp: new Date(),
      persona: 'Björk',
      notificationType: 'clarification',
      summary: 'Need more context on the requirements',
      urgency: 0.9,
      recommendation: 'Add a spec doc',
    } satisfies MeshEvent,
  },
};
