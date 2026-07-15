import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ACTIVITY_STORY_LIMIT, type ValkyrieEventTelemetry } from '../domain';
import {
  createMockOdinReviewService,
  createMockValkyrieService,
  createSeedReviewItems,
  createSeedValkyrieDashboard,
} from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ActivityPage } from './ActivityPage';

function serviceWithEvents(events: ValkyrieEventTelemetry[]) {
  const dashboard = createSeedValkyrieDashboard();
  dashboard.telemetry = { ...dashboard.telemetry!, recentEvents: events };
  return createMockValkyrieService(dashboard);
}

describe('ActivityPage', () => {
  it('shows the loading state first', () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('activity-loading')).toBeInTheDocument();
  });

  it('groups telemetry into stories and hides routine triage by default', async () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });

    const stories = await screen.findAllByTestId('story-investigation');
    expect(stories.length).toBeGreaterThan(0);
    expect(stories[0]).toHaveTextContent('Registry token rollover broke image pulls');
    // The ambient idle-triage story stays hidden until opted in.
    expect(screen.queryByTestId('story-triage')).toBeNull();
    expect(screen.queryByText('Triaged 6 routine signals — nothing needed')).toBeNull();
  });

  it('reveals collapsed idle-triage lines behind the show-routine toggle', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findAllByTestId('story-investigation');

    await user.click(screen.getByRole('button', { name: 'show routine' }));

    const triage = screen.getByTestId('story-triage');
    expect(triage).toHaveTextContent('Triaged 6 routine signals — nothing needed');
    expect(triage).toHaveTextContent('env-k8s-ymir');
    // A collapsed line, not an expandable thread.
    expect(within(triage).queryByRole('button')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'show routine' }));
    expect(screen.queryByTestId('story-triage')).toBeNull();
  });

  it('expands an investigation into its causal thread with action status', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findAllByTestId('story-investigation');

    await user.click(
      screen.getByRole('button', { name: /registry token rollover broke image pulls/i }),
    );

    const thread = screen.getByTestId('story-thread');
    const rows = within(thread).getAllByTestId('story-event');
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('Pod ravn-worker-77 stuck in ImagePullBackOff'),
      expect.stringContaining('Registry token rollover broke image pulls'),
      expect.stringContaining('ODIN approved refresh_pull_secret_and_restart'),
      expect.stringContaining('Refreshed the registry pull secret'),
    ]);
    expect(within(thread).getByTestId('story-action-status')).toHaveTextContent('completed');

    await user.click(
      screen.getByRole('button', { name: /registry token rollover broke image pulls/i }),
    );
    expect(screen.queryByTestId('story-thread')).toBeNull();
  });

  it('marks failed actions in the thread', async () => {
    const user = userEvent.setup();
    const events: ValkyrieEventTelemetry[] = [
      {
        id: 'evt-failed-action',
        eventType: 'valkyrie.action.failed',
        kind: 'action',
        environmentId: 'env-k8s-valhalla',
        summary: 'Rollout restart did not converge',
        observedAt: '2026-06-03T14:05:00Z',
        correlationId: 'corr-failed',
      },
    ];
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: serviceWithEvents(events) }),
    });
    await screen.findAllByTestId('story-investigation');

    await user.click(screen.getByRole('button', { name: /rollout restart did not converge/i }));

    expect(screen.getByTestId('story-action-status')).toHaveTextContent('failed');
  });

  it('opens the skill viewer from a judgment skill reference', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findAllByTestId('story-investigation');

    await user.click(
      screen.getByRole('button', { name: /registry token rollover broke image pulls/i }),
    );
    await user.click(await screen.findByTestId('skill-link-registry_token_refresh_check'));

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('pull-secret freshness'));

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('filters stories by environment', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findAllByTestId('story-investigation');

    await user.click(screen.getByRole('button', { name: 'show routine' }));
    expect(screen.getByTestId('story-triage')).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Filter by environment'), ['env-k8s-valhalla']);
    // The ymir-only triage story disappears; valhalla investigations stay.
    expect(screen.queryByTestId('story-triage')).toBeNull();
    expect(screen.getAllByTestId('story-investigation').length).toBeGreaterThan(0);

    await user.selectOptions(screen.getByLabelText('Filter by environment'), ['']);
    expect(screen.getByTestId('story-triage')).toBeInTheDocument();
  });

  it('keeps only stories with actions when actions-only is toggled', async () => {
    const user = userEvent.setup();
    const events: ValkyrieEventTelemetry[] = [
      {
        id: 'evt-signal-only',
        eventType: 'signal.kubernetes.event',
        kind: 'signal',
        environmentId: 'env-k8s-valhalla',
        summary: 'Disk pressure rising on node worker-3',
        observedAt: '2026-06-03T14:04:00Z',
        correlationId: 'corr-disk',
      },
      {
        id: 'evt-with-action',
        eventType: 'valkyrie.action.completed',
        kind: 'action',
        environmentId: 'env-k8s-valhalla',
        summary: 'Compacted the journal to free disk space',
        observedAt: '2026-06-03T14:05:00Z',
        correlationId: 'corr-journal',
      },
    ];
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: serviceWithEvents(events) }),
    });
    await screen.findAllByTestId('story-investigation');
    expect(screen.getAllByTestId('story-investigation')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'actions only' }));

    const stories = screen.getAllByTestId('story-investigation');
    expect(stories).toHaveLength(1);
    expect(stories[0]).toHaveTextContent('Compacted the journal');
  });

  it('caps the story list at ACTIVITY_STORY_LIMIT newest stories', async () => {
    const events: ValkyrieEventTelemetry[] = Array.from({ length: 40 }, (_, index) => ({
      id: `evt-flood-${index}`,
      eventType: 'signal.kubernetes.event',
      kind: 'signal' as const,
      environmentId: 'env-k8s-valhalla',
      summary: `Signal flood ${index}`,
      observedAt: `2026-06-03T13:${String(10 + index).padStart(2, '0')}:00Z`,
      correlationId: `corr-flood-${index}`,
    }));
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: serviceWithEvents(events) }),
    });

    const stories = await screen.findAllByTestId('story-investigation');
    expect(stories).toHaveLength(ACTIVITY_STORY_LIMIT);
    // The newest stories survive the cap, the oldest fall off.
    expect(stories[0]).toHaveTextContent('Signal flood 39');
    expect(screen.queryByText('Signal flood 0')).toBeNull();
  });

  it('switches to the raw debug list and back', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findAllByTestId('story-investigation');
    expect(screen.queryByTestId('activity-debug-list')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'debug view' }));

    const debugList = screen.getByTestId('activity-debug-list');
    const rows = within(debugList).getAllByTestId('activity-row');
    expect(rows.length).toBeGreaterThan(0);
    // Raw events keep their event types and correlation ids visible…
    expect(debugList).toHaveTextContent('valkyrie.judgment.proposed');
    expect(debugList).toHaveTextContent('corr corr-imagepull');
    // …and settled reviews still ride along.
    expect(debugList).toHaveTextContent('decided by human:operator');

    await user.click(screen.getByRole('button', { name: 'debug view' }));
    expect(screen.queryByTestId('activity-debug-list')).toBeNull();
    expect(screen.getAllByTestId('story-investigation').length).toBeGreaterThan(0);
  });

  it('explains when filters hide every story', async () => {
    const user = userEvent.setup();
    const events: ValkyrieEventTelemetry[] = [
      {
        id: 'evt-idle-only',
        eventType: 'valkyrie.judgment.proposed',
        kind: 'judgment',
        environmentId: 'env-k8s-ymir',
        summary: 'Triaged 3 routine signals — nothing needed',
        observedAt: '2026-06-03T14:04:00Z',
        correlationId: 'corr-idle-only',
        tier: 'ambient',
      },
    ];
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: serviceWithEvents(events) }),
    });

    const filteredEmpty = await screen.findByTestId('activity-filtered-empty');
    expect(filteredEmpty).toHaveTextContent('1 story is hidden by the current filters.');

    await user.click(screen.getByRole('button', { name: 'show routine' }));
    expect(screen.getByTestId('story-triage')).toBeInTheDocument();
  });

  it('points settled-decision-only activity at the debug view', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: serviceWithEvents([]) }),
    });

    const filteredEmpty = await screen.findByTestId('activity-filtered-empty');
    expect(filteredEmpty).toHaveTextContent(
      'No stories yet — settled decisions live in the debug view.',
    );

    await user.click(screen.getByRole('button', { name: 'debug view' }));
    const rows = await screen.findAllByTestId('activity-row');
    expect(rows[0]).toHaveTextContent('decided by human:operator');
    expect(rows.some((row) => row.textContent?.includes('autonomy'))).toBe(true);
  });

  it('shows an empty ledger state', async () => {
    const empty = createMockOdinReviewService(
      createSeedReviewItems().filter((item) => item.status === 'pending'),
    );
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: serviceWithEvents([]),
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
          pendingByEnvironment: {},
          countsByStatus: {},
        }),
    };
    render(<ActivityPage />, {
      wrapper: wrapWithValkyrie({ valkyrie: dashboard, 'valkyrie.reviews': broken }),
    });
    expect(await screen.findByTestId('activity-error')).toHaveTextContent('dashboard offline');
  });
});
