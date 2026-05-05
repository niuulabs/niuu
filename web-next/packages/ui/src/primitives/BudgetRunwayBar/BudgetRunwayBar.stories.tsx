import type { Meta, StoryObj } from '@storybook/react';
import { BudgetRunwayBar } from './BudgetRunwayBar';

const meta: Meta<typeof BudgetRunwayBar> = {
  title: 'Data Viz/BudgetRunwayBar',
  component: BudgetRunwayBar,
  decorators: [
    (Story) => (
      <div style={{ width: 320 }}>
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof BudgetRunwayBar>;

/** Morning: 20% spent, projecting 60%, day is 30% done. */
export const Morning: Story = {
  args: { spent: 20, projected: 60, cap: 100, elapsedFrac: 0.3 },
};

/** Midday: on track — spent matches projection. */
export const OnTrack: Story = {
  args: { spent: 45, projected: 90, cap: 100, elapsedFrac: 0.5 },
};

/** Burning hot — projected exceeds cap (over). */
export const OverCap: Story = {
  args: { spent: 75, projected: 130, cap: 100, elapsedFrac: 0.65 },
};

/** Idle — almost nothing spent even late in the day. */
export const Idle: Story = {
  args: { spent: 5, projected: 10, cap: 100, elapsedFrac: 0.8 },
};

/** Day complete — elapsed=1, near cap. */
export const EndOfDay: Story = {
  args: { spent: 90, projected: 90, cap: 100, elapsedFrac: 1 },
};

/** Zero cap edge case. */
export const ZeroCap: Story = {
  args: { spent: 0, projected: 0, cap: 0, elapsedFrac: 0.5 },
};

/** Full suite at various percentage combinations. */
export const AllScenarios: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, width: 320 }}>
      {[
        {
          label: '20% spent, 60% proj, 30% day',
          spent: 20,
          projected: 60,
          cap: 100,
          elapsedFrac: 0.3,
        },
        {
          label: '50% spent, 50% proj, 50% day',
          spent: 50,
          projected: 50,
          cap: 100,
          elapsedFrac: 0.5,
        },
        {
          label: '70% spent, 90% proj, 70% day',
          spent: 70,
          projected: 90,
          cap: 100,
          elapsedFrac: 0.7,
        },
        {
          label: '80% spent, 130% proj (over)',
          spent: 80,
          projected: 130,
          cap: 100,
          elapsedFrac: 0.75,
        },
        { label: '100% spent, 100% proj', spent: 100, projected: 100, cap: 100, elapsedFrac: 0.9 },
      ].map(({ label, ...props }) => (
        <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <code style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{label}</code>
          <BudgetRunwayBar {...props} />
        </div>
      ))}
    </div>
  ),
};
