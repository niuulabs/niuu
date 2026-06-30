import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  createMockOdinReviewService,
  createMockValkyrieService,
  createSeedReviewItems,
  createSeedValkyrieDashboard,
} from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ActivityPage } from './ActivityPage';

describe('ActivityPage', () => {
  it('shows the loading state first', () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('activity-loading')).toBeInTheDocument();
  });

  it('lists live telemetry from all valkyries first', async () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });

    const rows = await screen.findAllByTestId('activity-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]).toHaveTextContent('valkyrie.action.proposed');
    expect(rows.some((row) => row.textContent?.includes('env-k8s-valhalla'))).toBe(true);
    expect(rows.some((row) => row.textContent?.includes('env-k8s-ymir'))).toBe(true);
  });

  it('falls back to settled decisions when telemetry is empty', async () => {
    const dashboard = createSeedValkyrieDashboard();
    dashboard.telemetry = { ...dashboard.telemetry!, recentEvents: [] };
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: createMockValkyrieService(dashboard) }),
    });

    const rows = await screen.findAllByTestId('activity-row');
    expect(rows[0]).toHaveTextContent('decided by human:operator');
    expect(rows.some((row) => row.textContent?.includes('autonomy'))).toBe(true);
  });

  it('shows an empty ledger state', async () => {
    const empty = createMockOdinReviewService(
      createSeedReviewItems().filter((item) => item.status === 'pending'),
    );
    const dashboard = createSeedValkyrieDashboard();
    dashboard.telemetry = { ...dashboard.telemetry!, recentEvents: [] };
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: createMockValkyrieService(dashboard),
        'valkyrie.reviews': empty,
      }),
    });
    expect(await screen.findByTestId('activity-empty')).toHaveTextContent(
      'No telemetry or decisions yet.',
    );
  });

  it('surfaces service failures', async () => {
    const dashboard = {
      getDashboard: () => Promise.reject(new Error('dashboard offline')),
      updateAutonomy: () => Promise.reject(new Error('nope')),
    };
    const broken = {
      listReviews: () => Promise.reject(new Error('ledger offline')),
      getReview: () => Promise.resolve(null),
      decideReview: () => Promise.reject(new Error('nope')),
      getSummary: () =>
        Promise.resolve({
          pendingTotal: 0,
          pendingByKind: {},
          pendingByRisk: {},
          countsByStatus: {},
        }),
    };
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: dashboard, 'valkyrie.reviews': broken }),
    });
    expect(await screen.findByTestId('activity-error')).toHaveTextContent('dashboard offline');
  });
});
