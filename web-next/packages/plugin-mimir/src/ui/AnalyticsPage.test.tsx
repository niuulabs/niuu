import { describe, it, expect } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { AnalyticsPage } from './AnalyticsPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

function withMounts(overrides: Partial<IMimirService['mounts']>): IMimirService {
  const base = createMimirMockAdapter();
  return { ...base, mounts: { ...base.mounts, ...overrides } };
}

describe('AnalyticsPage', () => {
  it('shows loading state initially', () => {
    wrap(<AnalyticsPage />);
    expect(screen.getByText(/loading eval report/)).toBeInTheDocument();
    expect(screen.getByText(/loading query stats/)).toBeInTheDocument();
  });

  it('renders the overall metric tiles', async () => {
    wrap(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByTestId('eval-tiles')).toBeInTheDocument());
    const tiles = screen.getAllByTestId('eval-tile');
    expect(tiles).toHaveLength(4);
    const strip = within(screen.getByTestId('eval-tiles'));
    expect(strip.getByText('precision @5')).toBeInTheDocument();
    expect(strip.getByText('mrr')).toBeInTheDocument();
    expect(strip.getByText('recall @10')).toBeInTheDocument();
    expect(strip.getByText('0.82')).toBeInTheDocument();
    expect(strip.getByText('0.74')).toBeInTheDocument();
    expect(strip.getByText('0.91')).toBeInTheDocument();
    expect(strip.getByText('120')).toBeInTheDocument();
  });

  it('renders the per-category metrics table', async () => {
    wrap(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByTestId('category-table')).toBeInTheDocument());
    const rows = screen.getAllByTestId('category-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(screen.getByText('arch')).toBeInTheDocument();
    expect(screen.getByText('infra')).toBeInTheDocument();
  });

  it('renders zero-result queries from the recent log', async () => {
    wrap(<AnalyticsPage />);
    await waitFor(() =>
      expect(screen.getAllByTestId('zero-result-query').length).toBeGreaterThan(0),
    );
    const misses = screen.getAllByTestId('zero-result-query');
    expect(misses.map((m) => m.textContent).join(' ')).toContain('sleipnir retention policy');
  });

  it('renders the recent query log with hit counts', async () => {
    wrap(<AnalyticsPage />);
    await waitFor(() => expect(screen.getByTestId('query-log')).toBeInTheDocument());
    const rows = screen.getAllByTestId('query-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(screen.getByText(/1843 total · 37 zero-result/)).toBeInTheDocument();
  });

  it('shows an empty state when there is no eval report', async () => {
    const service = withMounts({ getEvalReport: async () => null });
    wrap(<AnalyticsPage />, service);
    await waitFor(() => expect(screen.getByTestId('eval-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('eval-tiles')).not.toBeInTheDocument();
  });

  it('shows an empty state when there are no query stats', async () => {
    const service = withMounts({ getQueryStats: async () => null });
    wrap(<AnalyticsPage />, service);
    await waitFor(() => expect(screen.getByTestId('queries-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('query-log')).not.toBeInTheDocument();
  });

  it('shows a no-misses message when no recent query has zero results', async () => {
    const service = withMounts({
      getQueryStats: async () => ({
        total: 10,
        zeroResultCount: 0,
        recent: [{ ts: '2026-04-19T10:00:00Z', query: 'arch', resultCount: 3 }],
      }),
    });
    wrap(<AnalyticsPage />, service);
    await waitFor(() => expect(screen.getByTestId('zero-empty')).toBeInTheDocument());
  });

  it('shows an empty recent log message when no queries are logged', async () => {
    const service = withMounts({
      getQueryStats: async () => ({ total: 0, zeroResultCount: 0, recent: [] }),
    });
    wrap(<AnalyticsPage />, service);
    await waitFor(() => expect(screen.getByTestId('recent-empty')).toBeInTheDocument());
  });

  it('shows a message when the eval report has no per-category metrics', async () => {
    const service = withMounts({
      getEvalReport: async () => ({
        generatedAt: '2026-04-19T04:30:00Z',
        overall: { precisionAt5: 0.5, mrr: 0.5, recallAt10: 0.5 },
        byCategory: {},
        queryCount: 1,
      }),
    });
    wrap(<AnalyticsPage />, service);
    await waitFor(() => expect(screen.getByText(/no per-category metrics/i)).toBeInTheDocument());
  });

  it('shows error states when the services throw', async () => {
    const service = withMounts({
      getEvalReport: async () => {
        throw new Error('eval service down');
      },
      getQueryStats: async () => {
        throw new Error('query stats down');
      },
    });
    wrap(<AnalyticsPage />, service);
    await waitFor(() => expect(screen.getByText('eval service down')).toBeInTheDocument());
    expect(screen.getByText('query stats down')).toBeInTheDocument();
  });
});
