import type { Meta, StoryObj } from '@storybook/react';
import { SessionEmptyChat } from './ChatEmptyStates';

const meta = {
  title: 'Chat/ChatEmptyStates',
  component: SessionEmptyChat,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof SessionEmptyChat>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    sessionName: 'Volundr',
    onSuggestionClick: (text) => console.log('Suggestion:', text),
  },
};
