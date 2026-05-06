import type { Meta, StoryObj } from '@storybook/react';
import { MentionPill } from './MentionPill';

const meta: Meta<typeof MentionPill> = {
  title: 'Chat/MentionPill',
  component: MentionPill,
};
export default meta;

export const Agent: StoryObj<typeof MentionPill> = {
  args: {
    mention: { kind: 'agent', participant: { peerId: 'peer-1', persona: 'Odin' } },
    onRemove: () => {},
  },
};

export const File: StoryObj<typeof MentionPill> = {
  args: {
    mention: { kind: 'file', entry: { name: 'index.ts', path: '/src/index.ts', type: 'file' } },
    onRemove: () => {},
  },
};
