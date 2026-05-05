import type { Meta, StoryObj } from '@storybook/react';
import { MentionMenu } from './MentionMenu';

const meta: Meta<typeof MentionMenu> = {
  title: 'Chat/MentionMenu',
  component: MentionMenu,
  decorators: [
    (Story) => (
      <div style={{ position: 'relative', height: 300, paddingTop: 260 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

const agents = [
  { kind: 'agent' as const, participant: { peerId: 'peer-1', persona: 'Odin' } },
  { kind: 'agent' as const, participant: { peerId: 'peer-2', persona: 'Frigg' } },
];

const files = [
  { kind: 'file' as const, entry: { name: 'src', path: '/src', type: 'directory' as const } },
  {
    kind: 'file' as const,
    entry: { name: 'index.ts', path: '/src/index.ts', type: 'file' as const },
  },
];

export const WithAgents: StoryObj<typeof MentionMenu> = {
  args: { items: agents, selectedIndex: 0, loading: false, onSelect: () => {}, onExpand: () => {} },
};

export const WithFiles: StoryObj<typeof MentionMenu> = {
  args: { items: files, selectedIndex: 0, loading: false, onSelect: () => {}, onExpand: () => {} },
};

export const Loading: StoryObj<typeof MentionMenu> = {
  args: { items: [], selectedIndex: 0, loading: true, onSelect: () => {}, onExpand: () => {} },
};

export const Empty: StoryObj<typeof MentionMenu> = {
  args: { items: [], selectedIndex: 0, loading: false, onSelect: () => {}, onExpand: () => {} },
};
