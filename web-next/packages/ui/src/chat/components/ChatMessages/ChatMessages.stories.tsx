import type { Meta, StoryObj } from '@storybook/react';
import { UserMessage, AssistantMessage, StreamingMessage, SystemMessage } from './ChatMessages';
import type { ChatMessage } from '../../types';

const now = new Date();

const userMsg: ChatMessage = {
  id: 'u1',
  role: 'user',
  content: 'What is the capital of France?',
  createdAt: now,
};
const assistantMsg: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'The capital of France is **Paris**.',
  createdAt: now,
  metadata: { usage: { 'claude-sonnet-4-6': { inputTokens: 15, outputTokens: 8 } } },
};
const systemMsg: ChatMessage = {
  id: 's1',
  role: 'system',
  content: 'Session connected',
  createdAt: now,
  metadata: { messageType: 'system' },
};

const meta: Meta = { title: 'Chat/Messages' };
export default meta;

export const User: StoryObj = { render: () => <UserMessage message={userMsg} /> };
export const Assistant: StoryObj = {
  render: () => <AssistantMessage message={assistantMsg} onCopy={() => {}} />,
};
export const Streaming: StoryObj = {
  render: () => (
    <StreamingMessage content="Generating a response to your question about France..." />
  ),
};
export const StreamingEmpty: StoryObj = { render: () => <StreamingMessage content="" /> };
export const System: StoryObj = { render: () => <SystemMessage message={systemMsg} /> };
