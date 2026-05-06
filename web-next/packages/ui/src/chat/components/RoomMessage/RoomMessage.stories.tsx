import type { Meta, StoryObj } from '@storybook/react';
import { RoomMessage } from './RoomMessage';

const now = new Date();
const meta: Meta<typeof RoomMessage> = { title: 'Chat/RoomMessage', component: RoomMessage };
export default meta;

export const User: StoryObj<typeof RoomMessage> = {
  args: {
    message: {
      id: '1',
      role: 'user',
      content: 'Hello from Odin',
      createdAt: now,
      participant: { peerId: 'p1', persona: 'Odin' },
    },
  },
};

export const Assistant: StoryObj<typeof RoomMessage> = {
  args: {
    message: {
      id: '2',
      role: 'assistant',
      content: 'Processed your request.',
      createdAt: now,
      participant: { peerId: 'p2', persona: 'Frigg' },
    },
  },
};
