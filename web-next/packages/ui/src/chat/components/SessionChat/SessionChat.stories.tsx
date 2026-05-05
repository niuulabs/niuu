import type { Meta, StoryObj } from '@storybook/react';
import { SessionChat } from './SessionChat';
import type { ChatMessage } from '../../types';

const messages: ChatMessage[] = [
  {
    id: 'msg-1',
    role: 'user',
    content: 'Can you review this code?',
    createdAt: new Date('2024-01-01T12:00:00'),
    status: 'done',
  },
  {
    id: 'msg-2',
    role: 'assistant',
    content: 'Sure! Looking at the code now...',
    createdAt: new Date('2024-01-01T12:00:10'),
    status: 'done',
  },
];

const meta = {
  title: 'Chat/SessionChat',
  component: SessionChat,
  parameters: { layout: 'fullscreen' },
  decorators: [
    (Story: React.ComponentType) => (
      <div style={{ height: '600px', display: 'flex', flexDirection: 'column' }}>
        <Story />
      </div>
    ),
  ],
  args: {
    messages,
    connected: true,
    historyLoaded: true,
    onSend: (text: string) => console.log('send:', text),
    onStop: () => console.log('stop'),
    sessionName: 'Volundr',
  },
} satisfies Meta<typeof SessionChat>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Empty: Story = {
  args: { messages: [] },
};

export const Disconnected: Story = {
  args: { connected: false },
};

export const LoadingHistory: Story = {
  args: { historyLoaded: false, connected: true },
};

export const Streaming: Story = {
  args: {
    streamingContent: 'Looking at the code... I can see several improvements.',
    streamingModel: 'claude-sonnet-4-6',
  },
};
