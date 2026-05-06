import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Session } from '../../../domain/session';
import type { UseMetricsResult } from '../../hooks/useMetrics';
import { MetricsTab } from './MetricsTab';
import { OverviewTab } from './OverviewTab';

const SESSION: Session = {
  id: 'sess-1',
  ravnId: 'ravn-7',
  personaName: 'Forge Operator',
  templateId: 'tpl-forge',
  clusterId: 'cluster-a',
  state: 'running',
  startedAt: '2026-05-03T12:00:00.000Z',
  readyAt: '2026-05-03T12:01:00.000Z',
  lastActivityAt: '2026-05-03T12:05:00.000Z',
  terminatedAt: '2026-05-03T12:10:00.000Z',
  resources: {
    cpuRequest: 2,
    cpuLimit: 4,
    cpuUsed: 1.5,
    memRequestMi: 4096,
    memLimitMi: 8192,
    memUsedMi: 6144,
    gpuCount: 1,
  },
  env: {
    LOG_LEVEL: 'debug',
  },
  events: [],
};

const METRICS: UseMetricsResult = {
  points: [
    { ts: '2026-05-03T12:00:00.000Z', cpu: 0.25, memMi: 2048, gpu: 0.1 },
    { ts: '2026-05-03T12:01:00.000Z', cpu: 0.5, memMi: 3072, gpu: 0.4 },
    { ts: '2026-05-03T12:02:00.000Z', cpu: 0.75, memMi: 4096, gpu: 0.6 },
  ],
  latest: { ts: '2026-05-03T12:02:00.000Z', cpu: 0.75, memMi: 4096, gpu: 0.6 },
};

describe('Volundr detail tabs', () => {
  it('renders overview metadata, resources, and environment values', () => {
    render(<OverviewTab session={SESSION} />);

    expect(screen.getByTestId('overview-tab')).toBeInTheDocument();
    expect(screen.getByText('Forge Operator')).toBeInTheDocument();
    expect(screen.getByText('cluster-a')).toBeInTheDocument();
    expect(screen.getByText('1 allocated')).toBeInTheDocument();
    expect(screen.getByText('LOG_LEVEL')).toBeInTheDocument();
    expect(screen.getByText('debug')).toBeInTheDocument();
    expect(screen.getByLabelText('CPU usage')).toHaveAttribute('aria-valuenow', '1.5');
  });

  it('renders metrics charts with latest values and waiting state', () => {
    const { rerender } = render(<MetricsTab metrics={METRICS} />);

    expect(screen.getByTestId('metrics-tab')).toBeInTheDocument();
    expect(screen.getByTestId('metrics-cpu')).toHaveTextContent('75.0%');
    expect(screen.getByTestId('metrics-mem')).toHaveTextContent('4096 Mi');
    expect(screen.getByTestId('metrics-gpu')).toHaveTextContent('0.60 frac');

    rerender(<MetricsTab metrics={{ points: [], latest: undefined }} />);
    expect(screen.getByTestId('metrics-waiting')).toBeInTheDocument();
  });
});
