import type { Meta, StoryObj } from '@storybook/react';
import { BudgetBar } from './BudgetBar';

const meta: Meta<typeof BudgetBar> = {
  title: 'Data Viz/BudgetBar',
  component: BudgetBar,
  args: { cap: 100, warnAt: 80 },
};
export default meta;

type Story = StoryObj<typeof BudgetBar>;

/** 20% — well under threshold, green. */
export const Low: Story = { args: { spent: 20 } };

/** 50% — safe. */
export const Mid: Story = { args: { spent: 50 } };

/** 80% — exactly at warn threshold, amber. */
export const AtWarn: Story = { args: { spent: 80 } };

/** 95% — approaching cap, amber. */
export const NearCap: Story = { args: { spent: 95 } };

/** 100% — at cap, critical red. */
export const AtCap: Story = { args: { spent: 100 } };

/** 130% — over cap, critical red. */
export const OverCap: Story = { args: { spent: 130 } };

/** Zero cap — edge case. */
export const ZeroCap: Story = { args: { spent: 0, cap: 0 } };

/** With dollar label rendered. */
export const WithLabel: Story = { args: { spent: 67.42, cap: 100, showLabel: true } };

/** Small size variant. */
export const SmallSize: Story = { args: { spent: 55, size: 'sm' } };

/** Full suite at all percentages. */
export const AllPercentages: Story = {
  render: () => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, width: 240 }}>
      {[0, 20, 50, 70, 80, 90, 95, 100, 120].map((pct) => (
        <div key={pct} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <code style={{ fontSize: 11, color: 'var(--color-text-muted)', width: 36 }}>{pct}%</code>
          <div style={{ flex: 1 }}>
            <BudgetBar spent={pct} cap={100} showLabel />
          </div>
        </div>
      ))}
    </div>
  ),
};
