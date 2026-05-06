import type { Meta, StoryObj } from '@storybook/react';
import { OutcomeCard } from './OutcomeCard';

const meta: Meta<typeof OutcomeCard> = {
  title: 'Chat/OutcomeCard',
  component: OutcomeCard,
};
export default meta;

type Story = StoryObj<typeof OutcomeCard>;

export const Pass: Story = {
  args: { raw: 'verdict: pass\nsummary: All tests passed\ntests: 42/42\nduration: 3.2s' },
};

export const Fail: Story = {
  args: { raw: 'verdict: fail\nsummary: 3 tests failed\ntests: 39/42' },
};

export const NeedsChanges: Story = {
  args: { raw: 'verdict: needs_changes\nsummary: Code quality below threshold\nissues: 5' },
};
