import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { createMockOdinReviewService, createSeedReviewItems } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { InboxPage } from './InboxPage';

function renderInbox(services: Record<string, unknown> = {}) {
  return render(<InboxPage />, { wrapper: wrapWithValkyrie(services) });
}

describe('InboxPage', () => {
  it('shows the loading state first', () => {
    renderInbox();
    expect(screen.getByTestId('inbox-loading')).toBeInTheDocument();
  });

  it('lists pending items sorted by urgency and selects the first', async () => {
    renderInbox();
    const cards = await screen.findAllByTestId('review-card');
    // The urgent court escalation outranks the build and the promotion.
    expect(cards[0]).toHaveTextContent('Action draft: k8s.restart_pod');
    expect(screen.getByTestId('review-detail')).toHaveTextContent('Action draft');
    expect(screen.getByTestId('inbox-pending-count')).toHaveTextContent('3 pending');
    expect(screen.getByText('1 high risk')).toBeInTheDocument();
  });

  it('opens the artifact tabs for a held build', async () => {
    const user = userEvent.setup();
    renderInbox();
    const cards = await screen.findAllByTestId('review-card');
    const buildCard = cards.find((card) =>
      card.textContent?.includes('valkyrie-inspect-kubernetes-pod-oomkilled'),
    );
    expect(buildCard).toBeDefined();
    await user.click(buildCard!);

    expect(screen.getByTestId('review-lineage')).toHaveTextContent('micro-dream build');
    await user.click(screen.getByRole('tab', { name: 'tool' }));
    expect(screen.getByTestId('review-artifact')).toHaveTextContent('def run(signal: dict)');
    await user.click(screen.getByRole('tab', { name: 'findings' }));
    expect(screen.getByTestId('review-artifact')).toHaveTextContent('guarded mode records');
  });

  it('approves an item and shows the verdict', async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findAllByTestId('review-card');

    await user.type(screen.getByLabelText('Decision reason'), 'safe to run');
    await user.click(screen.getByRole('button', { name: /approve/i }));

    await waitFor(() => {
      expect(screen.getByTestId('inbox-pending-count')).toHaveTextContent('2 pending');
    });
  });

  it('refuses to reject without a reason', async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findAllByTestId('review-card');

    await user.click(screen.getByRole('button', { name: /reject/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('A reason is required');
    expect(screen.getByTestId('inbox-pending-count')).toHaveTextContent('3 pending');
  });

  it('filters by kind', async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findAllByTestId('review-card');

    await user.selectOptions(screen.getByLabelText('Filter by kind'), 'skill_promotion');

    await waitFor(() => {
      const cards = screen.getAllByTestId('review-card');
      expect(cards).toHaveLength(1);
      expect(cards[0]).toHaveTextContent('proven-disk-pressure-probe');
    });
  });

  it('shows an empty state when nothing is pending', async () => {
    const empty = createMockOdinReviewService(
      createSeedReviewItems().filter((item) => item.status !== 'pending'),
    );
    renderInbox({ 'valkyrie.reviews': empty });
    expect(await screen.findByTestId('inbox-empty')).toHaveTextContent('Nothing needs you');
  });

  it('surfaces service failures', async () => {
    const broken = {
      listReviews: () => Promise.reject(new Error('queue offline')),
      getReview: () => Promise.resolve(null),
      decideReview: () => Promise.reject(new Error('nope')),
      getSummary: () => Promise.reject(new Error('queue offline')),
    };
    renderInbox({ 'valkyrie.reviews': broken });
    expect(await screen.findByTestId('inbox-error')).toHaveTextContent('queue offline');
  });
});
