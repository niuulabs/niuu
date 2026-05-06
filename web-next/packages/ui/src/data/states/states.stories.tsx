import type { Meta, StoryObj } from '@storybook/react';
import { EmptyState } from './EmptyState';
import { LoadingState } from './LoadingState';
import { ErrorState } from './ErrorState';

// ── EmptyState ─────────────────────────────────────────

const emptyMeta: Meta<typeof EmptyState> = {
  title: 'Data/EmptyState',
  component: EmptyState,
  args: { title: 'No items found' },
};
export default emptyMeta;

type EmptyStory = StoryObj<typeof EmptyState>;

export const Default: EmptyStory = {};

export const WithIcon: EmptyStory = {
  args: { icon: '🔍', title: 'No results', description: 'Try adjusting your filters.' },
};

export const WithAction: EmptyStory = {
  args: {
    icon: '📭',
    title: 'No sessions yet',
    description: 'Start a new session to get going.',
    action: <button>New Session</button>,
  },
};

// ── LoadingState ───────────────────────────────────────

export const Loading: StoryObj<typeof LoadingState> = {
  render: (args) => <LoadingState {...args} />,
  args: { label: 'Loading sessions…' },
};

export const LoadingDefault: StoryObj<typeof LoadingState> = {
  render: () => <LoadingState />,
};

// ── ErrorState ─────────────────────────────────────────

export const Error: StoryObj<typeof ErrorState> = {
  render: (args) => <ErrorState {...args} />,
  args: { message: 'Could not reach the server.' },
};

export const ErrorWithAction: StoryObj<typeof ErrorState> = {
  render: () => (
    <ErrorState
      icon="⚠️"
      title="Request failed"
      message="The server returned a 503."
      action={<button>Retry</button>}
    />
  ),
};
