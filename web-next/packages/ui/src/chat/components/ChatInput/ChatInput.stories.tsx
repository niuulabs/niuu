import type { Meta, StoryObj } from '@storybook/react';
import { ChatInput } from './ChatInput';
import type { RoomParticipant } from '../../types';

const participants: ReadonlyMap<string, RoomParticipant> = new Map([
  ['peer-1', { peerId: 'peer-1', persona: 'Ada', color: '#38bdf8' }],
]);

const meta = {
  title: 'Chat/ChatInput',
  component: ChatInput,
  parameters: { layout: 'centered' },
  decorators: [
    (Story: React.ComponentType) => (
      <div style={{ width: 600 }}>
        <Story />
      </div>
    ),
  ],
  args: {
    onSend: (text: string) => console.log('send:', text),
    onStop: () => console.log('stop'),
    isLoading: false,
    disabled: false,
  },
} satisfies Meta<typeof ChatInput>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Loading: Story = { args: { isLoading: true } };
export const Disabled: Story = { args: { disabled: true } };
export const WithParticipants: Story = { args: { participants } };
