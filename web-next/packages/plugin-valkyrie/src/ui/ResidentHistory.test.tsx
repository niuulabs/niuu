import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createMockValkyrieService } from '../adapters/mock';
import { LIST_LIMIT } from '../domain';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ActivityPage } from './ActivityPage';

describe('Activity history', () => {
  it('opens the signal browser with severity filtering', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('activity-page');

    await user.click(screen.getByRole('button', { name: /browse observed signals/i }));
    const browser = await screen.findByTestId('valkyrie-signal-browser');
    await waitFor(() => expect(browser).toHaveTextContent('pod/ravn-worker-77'));

    await user.selectOptions(within(browser).getByLabelText('Filter signals by severity'), [
      'critical',
    ]);
    await waitFor(() => expect(browser).toHaveTextContent('deployment/sleipnir-api'));
    expect(browser).not.toHaveTextContent('pod/ravn-worker-77');
  });

  it('lists stored decisions with their own summary sentences, outcome, and lineage on expand', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('activity-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    // Each row leads with the record's own readable sentence, not a canned label.
    await waitFor(() =>
      expect(decisions).toHaveTextContent('OOMKilled pattern handled with learned probe'),
    );
    expect(decisions).toHaveTextContent('Registry token rollover broke image pulls');
    // The imagepull decision genuinely awaits the operator (present tier +
    // human_review_required + a real action).
    expect(within(decisions).getByTestId('decision-needs-approval')).toHaveTextContent(
      'Needs your approval',
    );
    expect(decisions).toHaveTextContent('Action executed');

    await user.click(
      within(decisions).getByRole('button', {
        name: /oomkilled pattern handled with learned probe/i,
      }),
    );
    const detail = await screen.findByTestId('decision-detail-decision-oom-1');
    expect(detail).toHaveTextContent('Installed learning skill k8s_memory_pressure_probe');
    await waitFor(() => expect(detail).toHaveTextContent('triggered by'));
    expect(detail).toHaveTextContent('OOMKilled pattern matches learned memory pressure case');
    expect(detail).toHaveTextContent('resulting actions');
  });

  it('collapses re-judged decisions into one row and never mislabels ambient ones', async () => {
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('activity-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    // The three PV re-judgments share a correlationId → one row, ×3 badge,
    // latest timestamp, redundant "Valkyrie <id> in <env>" prefix stripped.
    await waitFor(() =>
      expect(within(decisions).getByTestId('decision-repeat-count')).toHaveTextContent('×3'),
    );
    expect(decisions).toHaveTextContent(
      'judged PersistentVolume media-primary healthy; capacity holding at 78%',
    );
    expect(decisions).not.toHaveTextContent('Valkyrie valkyrie-valhalla-sigrun in');
    // 6 stored decisions collapse to 4 visible rows.
    expect(within(decisions).getAllByTestId('decision-card')).toHaveLength(4);

    // The PV judgments are ambient with `watch` (not a real action): despite
    // their human_review_required authority they must NOT claim approval.
    const pvRow = within(decisions)
      .getAllByTestId('decision-card')
      .find((card) => card.textContent?.includes('PersistentVolume'));
    expect(pvRow).toBeDefined();
    expect(within(pvRow!).queryByTestId('decision-needs-approval')).toBeNull();
    expect(within(pvRow!).getByTestId('decision-status')).toHaveTextContent('Observation only');
    // Subtitle carries tier, confidence, and the evidence subject.
    expect(pvRow).toHaveTextContent('ambient');
    expect(pvRow).toHaveTextContent('71% confidence');
    expect(pvRow).toHaveTextContent('pv/media-primary');
  });

  it('caps the decisions fetch at LIST_LIMIT and says so when there are more', async () => {
    const service = createMockValkyrieService();
    const many = Array.from({ length: 25 }, (_, index) => ({
      decisionId: `decision-bulk-${index}`,
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      operationalState: 'watching',
      tier: 'ambient',
      confidence: 0.6,
      rationale: 'routine',
      recommendedAction: 'none',
      actionAuthority: 'autonomous',
      signalRefs: [],
      evidence: [],
      correlationId: `corr-bulk-${index}`,
      summary: `routine check ${index}`,
      outcome: '',
      decidedAt: `2026-06-03T13:${String(10 + index)}:00Z`,
    }));
    const listDecisions = vi.fn(async (filters: { limit?: number } = {}) => {
      const limit = filters.limit ?? 50;
      return { items: many.slice(0, limit), total: many.length, limit, offset: 0 };
    });
    const busy = { ...service, listDecisions };
    render(<ActivityPage />, { wrapper: wrapWithValkyrie({ valkyrie: busy }) });
    await screen.findByTestId('activity-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    await waitFor(() =>
      expect(within(decisions).getByTestId('decisions-total')).toHaveTextContent(
        `latest ${LIST_LIMIT} · 25 total`,
      ),
    );
    // Every fetch asked for at most the cap.
    for (const call of listDecisions.mock.calls) {
      expect(call[0]?.limit).toBe(LIST_LIMIT);
    }
    expect(within(decisions).getAllByTestId('decision-card')).toHaveLength(LIST_LIMIT);
  });

  it('marks failed action outcomes on decision cards', async () => {
    const service = createMockValkyrieService();
    const failing = {
      ...service,
      listDecisions: async () => ({
        items: [
          {
            decisionId: 'decision-failed',
            environmentId: 'env-k8s-valhalla',
            valkyrieId: 'valkyrie-valhalla-sigrun',
            operationalState: 'incident',
            tier: 'urgent',
            confidence: 0.5,
            rationale: 'Restart attempt did not converge.',
            recommendedAction: 'restart_deployment',
            actionAuthority: 'autonomous',
            signalRefs: [],
            evidence: [],
            correlationId: 'corr-x',
            summary: 'restart failed',
            outcome: 'failed',
            outcomeDetail: 'rollout timed out',
            decidedAt: '2026-06-03T14:00:00Z',
          },
        ],
        total: 1,
        limit: 8,
        offset: 0,
      }),
    };
    render(<ActivityPage />, { wrapper: wrapWithValkyrie({ valkyrie: failing }) });
    await screen.findByTestId('activity-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    await waitFor(() => expect(decisions).toHaveTextContent('Action failed'));
    // The headline is the record's own sentence, not the canned state label.
    expect(decisions).toHaveTextContent('restart failed');
  });

  it('pages the signal browser when history exceeds one page', async () => {
    const user = userEvent.setup();
    const service = createMockValkyrieService();
    const manySignals = Array.from({ length: 15 }, (_, index) => ({
      signalId: `sig-${index}`,
      environmentId: 'env-k8s-valhalla',
      eventType: 'signal.kubernetes.event',
      source: 'adapter:k8s-events',
      subject: `pod/worker-${index}`,
      summary: `signal ${index}`,
      severity: 'info',
      receivedAt: `2026-06-03T13:${String(10 + index)}:00Z`,
    }));
    const paged = {
      ...service,
      listSignalHistory: async (filters: { limit?: number; offset?: number } = {}) => {
        const offset = filters.offset ?? 0;
        const limit = filters.limit ?? 10;
        return {
          items: manySignals.slice(offset, offset + limit),
          total: manySignals.length,
          limit,
          offset,
        };
      },
    };
    render(<ActivityPage />, { wrapper: wrapWithValkyrie({ valkyrie: paged }) });
    await screen.findByTestId('activity-page');

    await user.click(screen.getByRole('button', { name: /browse observed signals/i }));
    const browser = await screen.findByTestId('valkyrie-signal-browser');
    await waitFor(() => expect(browser).toHaveTextContent('1–10 of 15'));

    await user.click(within(browser).getByRole('button', { name: 'older' }));
    await waitFor(() => expect(browser).toHaveTextContent('11–15 of 15'));

    await user.click(within(browser).getByRole('button', { name: 'newer' }));
    await waitFor(() => expect(browser).toHaveTextContent('1–10 of 15'));

    await user.click(screen.getByRole('button', { name: /hide observed signals/i }));
    expect(screen.queryByTestId('valkyrie-signal-browser')).not.toBeInTheDocument();
  });

  it('says loudly when the backend has no decision history endpoints', async () => {
    const user = userEvent.setup();
    const service = createMockValkyrieService();
    const oldBackend = {
      ...service,
      listDecisions: () => Promise.reject(new Error('404 Not Found')),
      listSignalHistory: () => Promise.reject(new Error('404 Not Found')),
    };
    render(<ActivityPage />, { wrapper: wrapWithValkyrie({ valkyrie: oldBackend }) });
    await screen.findByTestId('activity-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    await waitFor(() => expect(decisions).toHaveTextContent('does not serve decision history yet'));

    await user.click(screen.getByRole('button', { name: /browse observed signals/i }));
    const browser = await screen.findByTestId('valkyrie-signal-browser');
    await waitFor(() => expect(browser).toHaveTextContent('does not serve decision history yet'));
  });

  it('links the skill inside an expanded decision detail', async () => {
    const user = userEvent.setup();
    window.history.pushState({}, '', '/valkyrie/activity?environment=env-k8s-valhalla');
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('activity-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    await waitFor(() =>
      expect(decisions).toHaveTextContent('OOMKilled pattern handled with learned probe'),
    );
    await user.click(
      within(decisions).getByRole('button', {
        name: /oomkilled pattern handled with learned probe/i,
      }),
    );

    const detail = await screen.findByTestId('decision-detail-decision-oom-1');
    await user.click(within(detail).getByTestId('skill-link-k8s_memory_pressure_probe'));
    await waitFor(() =>
      expect(screen.getByRole('dialog')).toHaveTextContent('def test_matches_oomkilled_pod'),
    );
  });

  it('shows operational state, for the chosen environment', async () => {
    const user = userEvent.setup();
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-operational-state')).toHaveTextContent(
      'Cluster capacity',
    );

    await user.selectOptions(screen.getByLabelText('Filter by environment'), 'env-host-jozef');

    await waitFor(() => expect(screen.queryByText('Cluster capacity')).not.toBeInTheDocument());
  });

  it('opens on the environment a resident page linked to', async () => {
    window.history.pushState({}, '', '/valkyrie/activity?environment=env-k8s-valhalla');
    render(<ActivityPage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByLabelText('Filter by environment')).toHaveValue('env-k8s-valhalla');
    window.history.pushState({}, '', '/');
  });
});
