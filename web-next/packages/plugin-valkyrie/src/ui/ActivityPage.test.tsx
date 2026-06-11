import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { createMockOdinReviewService, createSeedReviewItems } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ActivityPage } from './ActivityPage';

describe('ActivityPage', () => {
  it('shows the loading state first', () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('activity-loading')).toBeInTheDocument();
  });

  it('lists settled decisions with who and why', async () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });

    const rows = await screen.findAllByTestId('activity-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]).toHaveTextContent('decided by human:operator');
    expect(rows.some((row) => row.textContent?.includes('autonomy'))).toBe(true);
  });

  it('filters by kind', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findAllByTestId('activity-row');

    await user.selectOptions(screen.getByLabelText('Filter activity by kind'), 'autonomy_change');

    await waitFor(() => {
      const rows = screen.getAllByTestId('activity-row');
      expect(rows).toHaveLength(1);
      expect(rows[0]).toHaveTextContent('Set valkyrie-ymir-k8s autonomy to autonomous');
    });
  });

  it('shows an empty ledger state', async () => {
    const empty = createMockOdinReviewService(
      createSeedReviewItems().filter((item) => item.status === 'pending'),
    );
    render(<ActivityPage />, { wrapper: wrapWithValkyrie({ 'valkyrie.reviews': empty }) });
    expect(await screen.findByTestId('activity-empty')).toHaveTextContent('one auditable ledger');
  });

  it('surfaces service failures', async () => {
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
    render(<ActivityPage />, { wrapper: wrapWithValkyrie({ 'valkyrie.reviews': broken }) });
    expect(await screen.findByTestId('activity-error')).toHaveTextContent('ledger offline');
  });
});
