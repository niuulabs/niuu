import type { Meta, StoryObj } from '@storybook/react';
import { ThreadGroup } from './ThreadGroup';
import type { ChatMessage } from '../../types';

const now = new Date();
const messages: ChatMessage[] = [
  {
    id: 'm1',
    role: 'assistant',
    content: 'Analyzing the problem...',
    createdAt: now,
    participant: { peerId: 'p1', persona: 'Odin' },
  },
  {
    id: 'm2',
    role: 'assistant',
    content: 'Found a solution.',
    createdAt: now,
    participant: { peerId: 'p1', persona: 'Odin' },
  },
];

const meta: Meta<typeof ThreadGroup> = {
  title: 'Chat/ThreadGroup',
  component: ThreadGroup,
};
export default meta;

export const Collapsed: StoryObj<typeof ThreadGroup> = {
  args: { messages, isCollapsed: true, onToggle: () => {} },
};
export const Expanded: StoryObj<typeof ThreadGroup> = {
  args: { messages, isCollapsed: false, onToggle: () => {} },
};
