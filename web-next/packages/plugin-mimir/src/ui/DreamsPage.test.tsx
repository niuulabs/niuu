import { describe, it, expect } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { DreamsPage } from './DreamsPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

describe('DreamsPage', () => {
  // ── Dream cycles ─────────────────────────────────────────────────────────

  it('renders the page title', () => {
    wrap(<DreamsPage />);
    expect(screen.getByRole('heading', { name: /dreams/i })).toBeInTheDocument();
  });

  it('shows loading state initially', () => {
    wrap(<DreamsPage />);
    expect(screen.getByText(/loading dream cycles/)).toBeInTheDocument();
  });

  it('renders dream cycle entries after load', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dream-cycle').length).toBeGreaterThan(0));
  });

  it('shows ravn chip for each cycle', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dream-cycle').length).toBeGreaterThan(0));
    expect(screen.getAllByText('ravn-fjolnir').length).toBeGreaterThan(0);
  });

  it('shows pages updated count', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dream-pages').length).toBeGreaterThan(0));
    const pagesStats = screen.getAllByTestId('dream-pages');
    expect(pagesStats[0]).toHaveTextContent(/pages updated/);
  });

  it('shows entities created count', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dream-entities').length).toBeGreaterThan(0));
    const entStats = screen.getAllByTestId('dream-entities');
    expect(entStats[0]).toHaveTextContent(/entities created/);
  });

  it('shows lint fixes count', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dream-fixes').length).toBeGreaterThan(0));
    const fixStats = screen.getAllByTestId('dream-fixes');
    expect(fixStats[0]).toHaveTextContent(/lint fixes/);
  });

  it('shows error state when service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getDreamCycles: async () => {
          throw new Error('dreams service down');
        },
      },
    };
    wrap(<DreamsPage />, failing);
    await waitFor(() => expect(screen.getByText('dreams service down')).toBeInTheDocument());
  });

  it('shows empty message when no cycles exist', async () => {
    const empty: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getDreamCycles: async () => [],
      },
    };
    wrap(<DreamsPage />, empty);
    await waitFor(() => expect(screen.getByText(/no dream cycles recorded/i)).toBeInTheDocument());
  });

  it('renders mount chips for each cycle', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('dream-cycle').length).toBeGreaterThan(0));
    expect(screen.getAllByText('local').length).toBeGreaterThan(0);
    expect(screen.getAllByText('shared').length).toBeGreaterThan(0);
  });

  // ── Activity log ──────────────────────────────────────────────────────────

  it('renders activity log section', async () => {
    wrap(<DreamsPage />);
    await waitFor(() =>
      expect(screen.getByRole('region', { name: /activity log/i })).toBeInTheDocument(),
    );
  });

  it('shows activity log rows after load', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('activity-row').length).toBeGreaterThan(0));
  });

  it('shows kind labels on activity rows', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('activity-kind').length).toBeGreaterThan(0));
    const kinds = screen.getAllByTestId('activity-kind').map((el) => el.textContent);
    expect(kinds.some((k) => k === 'write')).toBe(true);
  });

  it('renders all kind filter buttons', async () => {
    wrap(<DreamsPage />);
    for (const kind of ['all', 'write', 'ingest', 'lint', 'dream']) {
      expect(screen.getByTestId(`kind-filter-${kind}`)).toBeInTheDocument();
    }
  });

  it('all filter is active by default', () => {
    wrap(<DreamsPage />);
    expect(screen.getByTestId('kind-filter-all')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('kind-filter-write')).toHaveAttribute('aria-pressed', 'false');
  });

  it('filters activity events by kind when a filter is clicked', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getAllByTestId('activity-row').length).toBeGreaterThan(0));

    const allCount = screen.getAllByTestId('activity-row').length;

    fireEvent.click(screen.getByTestId('kind-filter-lint'));

    await waitFor(() => {
      const rows = screen.getAllByTestId('activity-row');
      expect(rows.length).toBeLessThan(allCount);
      rows.forEach((row) => {
        expect(row.querySelector('[data-testid="activity-kind"]')?.textContent).toBe('lint');
      });
    });
  });

  it('marks clicked filter as active', async () => {
    wrap(<DreamsPage />);
    fireEvent.click(screen.getByTestId('kind-filter-write'));
    expect(screen.getByTestId('kind-filter-write')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('kind-filter-all')).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows empty message when no events match filter', async () => {
    const noQuery: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getActivityLog: async () => [
          {
            id: 'act-x',
            timestamp: '2026-04-19T10:00:00Z',
            kind: 'write',
            mount: 'local',
            ravn: 'ravn-fjolnir',
            message: 'test write event',
          },
        ],
      },
    };
    wrap(<DreamsPage />, noQuery);
    await waitFor(() => expect(screen.getAllByTestId('activity-row').length).toBe(1));

    fireEvent.click(screen.getByTestId('kind-filter-dream'));

    await waitFor(() =>
      expect(screen.getByTestId('activity-empty')).toHaveTextContent(/kind "dream"/),
    );
  });

  it('shows activity log loading state', () => {
    wrap(<DreamsPage />);
    expect(screen.getByText(/loading activity log/)).toBeInTheDocument();
  });

  it('shows entry count in activity log header', async () => {
    wrap(<DreamsPage />);
    await waitFor(() => expect(screen.getByText(/append-only · \d+ entries/)).toBeInTheDocument());
  });

  it('shows error state when activity log service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      lint: {
        ...createMimirMockAdapter().lint,
        getActivityLog: async () => {
          throw new Error('activity log unavailable');
        },
      },
    };
    wrap(<DreamsPage />, failing);
    await waitFor(() => expect(screen.getByText('activity log unavailable')).toBeInTheDocument());
  });
});
