import type { Meta, StoryObj } from '@storybook/react';
import { SlashCommandMenu } from './SlashCommandMenu';

const meta: Meta<typeof SlashCommandMenu> = {
  title: 'Chat/SlashCommandMenu',
  component: SlashCommandMenu,
  decorators: [
    (Story) => (
      <div style={{ position: 'relative', height: 250, paddingTop: 220 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

export const Default: StoryObj<typeof SlashCommandMenu> = {
  args: {
    commands: [
      { name: 'clear', type: 'command' },
      { name: 'compact', type: 'command' },
      { name: 'summarize', type: 'skill' },
    ],
    selectedIndex: 0,
    onSelect: () => {},
  },
};

export const Empty: StoryObj<typeof SlashCommandMenu> = {
  args: { commands: [], selectedIndex: 0, onSelect: () => {} },
};
