import type { Meta, StoryObj } from '@storybook/react';
import { KpiStrip } from './KpiStrip';
import { KpiCard } from './KpiCard';

const meta: Meta<typeof KpiStrip> = {
  title: 'Data/KpiStrip',
  component: KpiStrip,
};
export default meta;

type Story = StoryObj<typeof KpiStrip>;

export const Default: Story = {
  render: () => (
    <KpiStrip>
      <KpiCard
        label="Total Sessions"
        value="1,204"
        delta="+8%"
        deltaTrend="up"
        deltaLabel="vs last week"
      />
      <KpiCard label="Active Agents" value={42} delta="-3" deltaTrend="down" />
      <KpiCard label="Avg Duration" value="4m 12s" delta="±2s" deltaTrend="neutral" />
      <KpiCard label="Error Rate" value="0.4%" delta="-0.1%" deltaTrend="up" />
    </KpiStrip>
  ),
};

export const WithSparkline: Story = {
  render: () => (
    <KpiStrip>
      <KpiCard
        label="Throughput"
        value="3.2k/s"
        delta="+400/s"
        deltaTrend="up"
        sparkline={
          <svg width={48} height={20} viewBox="0 0 48 20" style={{ display: 'block' }}>
            <polyline
              points="0,18 8,14 16,10 24,12 32,6 40,4 48,2"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
            />
          </svg>
        }
      />
      <KpiCard label="Queue Depth" value={128} />
    </KpiStrip>
  ),
};

export const SingleCard: Story = {
  render: () => <KpiCard label="Uptime" value="99.98%" delta="+0.01%" deltaTrend="up" />,
};
