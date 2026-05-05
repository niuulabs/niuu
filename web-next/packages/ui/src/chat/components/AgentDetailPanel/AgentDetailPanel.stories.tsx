import type { Meta, StoryObj } from '@storybook/react';
import { AgentDetailPanel } from './AgentDetailPanel';
import type { RoomParticipant, AgentInternalEvent } from '../../types';

const participant: RoomParticipant = {
  peerId: 'peer-1',
  persona: 'Ada',
  displayName: 'Ada Lovelace',
  status: 'thinking',
  color: '#38bdf8',
};

const events: AgentInternalEvent[] = [
  { id: 'ev-1', frameType: 'thought', data: 'Analyzing the code structure...' },
  {
    id: 'ev-2',
    frameType: 'tool_start',
    data: 'read_file',
    metadata: { tool_name: 'read_file', input: { path: 'src/index.ts' } },
  },
  {
    id: 'ev-3',
    frameType: 'tool_result',
    data: 'export default {}',
    metadata: { tool_name: 'read_file' },
  },
];

const meta = {
  title: 'Chat/AgentDetailPanel',
  component: AgentDetailPanel,
  parameters: { layout: 'centered' },
  decorators: [
    (Story: React.ComponentType) => (
      <div style={{ width: 360, height: 500, position: 'relative' }}>
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof AgentDetailPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: { participant, events, onClose: () => console.log('close') },
};

export const Empty: Story = {
  args: { participant: { ...participant, status: 'idle' }, events: [], onClose: () => {} },
};
