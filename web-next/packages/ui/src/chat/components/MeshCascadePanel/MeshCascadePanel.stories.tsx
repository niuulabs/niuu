import type { Meta, StoryObj } from '@storybook/react';
import { MeshCascadePanel } from './MeshCascadePanel';
import type { MeshEvent } from '../../types';

const events: MeshEvent[] = [
  {
    id: 'e1',
    type: 'outcome',
    participantId: 'peer-1',
    participant: { color: '#38bdf8' },
    timestamp: new Date(),
    persona: 'Ada',
    eventType: 'review',
    verdict: 'pass',
    summary: 'All checks passed. Code looks good.',
  },
  {
    id: 'e2',
    type: 'mesh_message',
    participantId: 'peer-1',
    participant: { color: '#38bdf8' },
    timestamp: new Date(),
    fromPersona: 'Ada',
    eventType: 'delegate',
    preview: 'Delegating test writing to Björk',
  },
  {
    id: 'e3',
    type: 'notification',
    participantId: 'peer-2',
    participant: { color: '#a78bfa' },
    timestamp: new Date(),
    persona: 'Björk',
    notificationType: 'clarification',
    summary: 'Need more context on the test requirements',
    urgency: 0.8,
  },
];

const meta = {
  title: 'Chat/MeshCascadePanel',
  component: MeshCascadePanel,
  parameters: { layout: 'centered' },
  decorators: [
    (Story: React.ComponentType) => (
      <div style={{ width: 360, height: 600 }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof MeshCascadePanel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    events,
    onEventClick: (e: MeshEvent) => console.log('click', e),
  },
};
