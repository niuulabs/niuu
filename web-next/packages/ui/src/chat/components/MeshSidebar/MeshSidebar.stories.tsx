import type { Meta, StoryObj } from '@storybook/react';
import { MeshSidebar } from './MeshSidebar';
import type { RoomParticipant } from '../../types';

const participants: ReadonlyMap<string, RoomParticipant> = new Map([
  [
    'peer-1',
    {
      peerId: 'peer-1',
      persona: 'Ada',
      displayName: 'Ada',
      participantType: 'ravn',
      status: 'thinking',
      color: '#38bdf8',
      tools: ['bash', 'read_file'],
      subscribesTo: ['task_done'],
    },
  ],
  [
    'peer-2',
    {
      peerId: 'peer-2',
      persona: 'Björk',
      participantType: 'ravn',
      status: 'idle',
      color: '#a78bfa',
      emits: ['review_complete'],
    },
  ],
]);

const meta = {
  title: 'Chat/MeshSidebar',
  component: MeshSidebar,
  parameters: { layout: 'centered' },
  decorators: [
    (Story: React.ComponentType) => (
      <div style={{ width: 200, height: 400 }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof MeshSidebar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    participants,
    selectedPeerId: null,
    onSelectPeer: (id: string) => console.log('select', id),
  },
};

export const WithSelection: Story = {
  args: {
    participants,
    selectedPeerId: 'peer-1',
    onSelectPeer: (id: string) => console.log('select', id),
  },
};
