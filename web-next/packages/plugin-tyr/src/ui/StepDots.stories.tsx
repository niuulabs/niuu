import type { Meta, StoryObj } from '@storybook/react';
import { StepDots } from './StepDots';
import { PLAN_STEPS } from '../domain/plan';

const meta: Meta<typeof StepDots> = {
  title: 'plugin-tyr/StepDots',
  component: StepDots,
  parameters: {
    layout: 'padded',
    backgrounds: { default: 'dark' },
  },
  argTypes: {
    current: {
      control: 'select',
      options: PLAN_STEPS,
    },
  },
};

export default meta;
type Story = StoryObj<typeof StepDots>;

export const Prompt: Story = {
  args: { steps: PLAN_STEPS, current: 'prompt' },
};

export const Questions: Story = {
  args: { steps: PLAN_STEPS, current: 'questions' },
};

export const Raiding: Story = {
  args: { steps: PLAN_STEPS, current: 'raiding' },
};

export const Draft: Story = {
  args: { steps: PLAN_STEPS, current: 'draft' },
};

export const Approved: Story = {
  args: { steps: PLAN_STEPS, current: 'approved' },
};
