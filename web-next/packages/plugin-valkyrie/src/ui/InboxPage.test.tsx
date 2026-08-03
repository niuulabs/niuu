import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

// HighlightedCode lazily loads shiki; reject it so the deterministic plain-text
// fallback (which carries the real code) renders in tests.
vi.mock('shiki', () => ({
  codeToHtml: vi.fn().mockRejectedValue(new Error('no highlight in test')),
}));
import { createMockOdinReviewService, createSeedReviewItems } from '../adapters/mock';
import { LIST_LIMIT, type ReviewItem } from '../domain';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { InboxPage } from './InboxPage';

function morningBriefItem(): ReviewItem {
  return {
    itemId: 'review:morning_brief:env-k8s-valhalla:2026-06-03',
    kind: 'morning_brief',
    requestedAction: 'acknowledge',
    environmentId: 'env-k8s-valhalla',
    valkyrieId: '',
    title: 'Morning brief — env-k8s-valhalla',
    summary: '37 signal(s) observed, 3 decision(s) taken in the last brief window.',
    audience: 'environment',
    flockId: '',
    domain: '',
    riskClass: 'low',
    safetyClass: 'read_only',
    urgency: 0.2,
    requestedCapability: 'approve',
    evidence: {
      brief_markdown:
        '# Morning brief — env-k8s-valhalla\n\nSignals observed: 37\nDecisions taken: 3',
      decision_count: 3,
      signal_count: 37,
    },
    status: 'pending',
    requestedBy: 'ravn:valkyrie-brief',
    requestedAt: '2026-06-03T06:00:00Z',
    decidedBy: '',
    decidedAt: '',
    decisionReason: '',
    resolvedAt: '',
    applyOutcome: '',
    applyDetail: '',
  };
}

function operatorQuestionItem(): ReviewItem {
  return {
    ...createSeedReviewItems()[0]!,
    itemId: 'review:court_escalation:operator:case-etcd',
    kind: 'court_escalation',
    requestedAction: 'answer_operator_question',
    title: 'Eir asks: May I inspect etcd logs?',
    summary: 'Operator approval is required.',
    evidence: {
      operator_question: {
        case_id: 'case-etcd',
        question: 'May I inspect etcd logs?',
      },
    },
    status: 'pending',
  };
}

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

    expect(screen.getByTestId('review-lineage')).toHaveTextContent('investigation build');
    await user.click(screen.getByRole('button', { name: 'Tool' }));
    expect(screen.getByTestId('review-artifact')).toHaveTextContent('def run(signal: dict)');
    await user.click(screen.getByRole('button', { name: 'Findings' }));
    expect(screen.getByTestId('review-artifact')).toHaveTextContent('guarded mode records');
  });

  it('shows the investigation ticket that produced the build', async () => {
    const user = userEvent.setup();
    renderInbox();
    const cards = await screen.findAllByTestId('review-card');
    const buildCard = cards.find((card) =>
      card.textContent?.includes('valkyrie-inspect-kubernetes-pod-oomkilled'),
    );
    await user.click(buildCard!);

    // The ticket tab is the first segment and renders by default.
    const ticketTab = screen.getByRole('button', { name: 'Ticket' });
    expect(ticketTab).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('review-artifact')).toHaveTextContent(
      'No installed instrument matches this capability',
    );
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

  it('answers resident questions directly with yes or no', async () => {
    const user = userEvent.setup();
    const service = createMockOdinReviewService([operatorQuestionItem()]);
    const decide = vi.spyOn(service, 'decideReview');
    renderInbox({ 'valkyrie.reviews': service });

    expect(await screen.findAllByText('Eir asks: May I inspect etcd logs?')).toHaveLength(2);
    expect(screen.getByLabelText('Operator answer')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'No' }));

    expect(decide).toHaveBeenCalledWith(
      expect.objectContaining({
        decision: 'rejected',
        reason: 'No, do not proceed.',
      }),
    );
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

  it('searches and filters the full queue, then groups the visible page', async () => {
    const user = userEvent.setup();
    renderInbox();
    await screen.findAllByTestId('review-card');

    await user.selectOptions(screen.getByLabelText('Filter by environment'), 'env-noatun');
    await user.selectOptions(screen.getByLabelText('Filter by risk'), 'high');
    await user.type(screen.getByLabelText('Search review inbox'), 'checkout');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(screen.getAllByTestId('review-card')).toHaveLength(1);
    });
    expect(screen.getByTestId('inbox-list-range')).toHaveTextContent('1–1 of 1 matching');
    expect(screen.getByText('env-noatun · 1')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Group reviews by'), 'risk');
    expect(screen.getByText('high risk · 1')).toBeInTheDocument();
  });

  it('renders a morning brief with its digest markdown and effect statement', async () => {
    const user = userEvent.setup();
    const withBrief = createMockOdinReviewService([...createSeedReviewItems(), morningBriefItem()]);
    renderInbox({ 'valkyrie.reviews': withBrief });

    const cards = await screen.findAllByTestId('review-card');
    const briefCard = cards.find((card) => card.textContent?.includes('Morning brief'));
    expect(briefCard).toBeDefined();
    expect(briefCard).toHaveTextContent('brief');
    await user.click(briefCard!);

    expect(screen.getByTestId('review-lineage')).toHaveTextContent('daily digest');
    expect(await screen.findByTestId('review-brief')).toHaveTextContent('Signals observed: 37');
    expect(screen.getByTestId('review-decision')).toHaveTextContent(
      'Approving marks this brief as read',
    );
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

  it('pages through the full queue while rendering at most LIST_LIMIT items', async () => {
    const user = userEvent.setup();
    const base = createSeedReviewItems()[0]!;
    const many = Array.from({ length: 25 }, (_, index) => ({
      ...base,
      itemId: `review:bulk:${index}`,
      title: `Bulk review ${index}`,
      status: 'pending' as const,
      requestedAt: `2026-06-03T${String(10 + Math.floor(index / 10))}:${String(
        10 + (index % 10),
      )}:00Z`,
    }));
    const service = createMockOdinReviewService(many);
    const listSpy = vi.spyOn(service, 'listReviews');
    renderInbox({ 'valkyrie.reviews': service });

    const cards = await screen.findAllByTestId('review-card');
    expect(cards).toHaveLength(LIST_LIMIT);
    expect(screen.getByTestId('inbox-pending-count')).toHaveTextContent('25 pending');
    expect(screen.getByTestId('inbox-list-range')).toHaveTextContent('1–20 of 25 matching');
    expect(listSpy).toHaveBeenCalledWith(
      expect.objectContaining({ limit: LIST_LIMIT + 1, offset: 0 }),
    );

    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(screen.getAllByTestId('review-card')).toHaveLength(5));
    expect(screen.getByTestId('inbox-list-range')).toHaveTextContent('21–25 of 25 matching');
    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });
});
