import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import {
  createMockRealmGovernanceService,
  createMockValkyrieService,
  createSeedRealms,
  createSeedValkyrieDashboard,
} from '../adapters/mock';
import { LIST_LIMIT } from '../domain';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { ValkyrieConsolePage } from './ValkyrieConsolePage';

describe('ValkyrieConsolePage', () => {
  it('shows the loading state first', () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('valkyrie-console-loading')).toBeInTheDocument();
  });

  it('renders the rich console with roster, situation, timeline, and authority panels', async () => {
    const seed = createSeedValkyrieDashboard();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-console-page')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-roster')).toHaveTextContent(seed.valkyries[0]!.name);
    expect(screen.getByTestId('valkyrie-console-hero')).toHaveTextContent(
      `valkyrie:${seed.valkyries[0]!.name}`,
    );
    expect(screen.getByTestId('valkyrie-current-situation')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-signal-timeline')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-authority')).toHaveTextContent('Authority & autonomy');
  });

  it('surfaces dashboard failures', async () => {
    const broken = {
      getDashboard: () => Promise.reject(new Error('dashboard offline')),
      updateAutonomy: () => Promise.reject(new Error('nope')),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: broken }) });
    expect(await screen.findByTestId('valkyrie-console-error')).toHaveTextContent(
      'dashboard offline',
    );
  });

  it('shows the charter and a human-readable authority boundary', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    expect(screen.getByTestId('valkyrie-charter')).toHaveTextContent(
      'Keep the Valhalla cluster healthy and boring',
    );
    // Sigrun is autonomous: the boundary explains the mode, not hardcoded prose.
    expect(screen.getByTestId('valkyrie-console-hero')).toHaveTextContent(
      'Acts on its own within its authority boundary',
    );
  });

  it('synthesizes the current situation from the latest decision', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const situation = screen.getByTestId('valkyrie-current-situation');
    await waitFor(() =>
      expect(situation).toHaveTextContent('ImagePullBackOff started right after'),
    );
    expect(situation).toHaveTextContent('Investigating');
    expect(situation).toHaveTextContent('82% confidence');
    expect(situation).toHaveTextContent('Waiting in the review inbox');
  });

  it('opens the signal browser with severity filtering', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

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
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

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
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

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
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: busy }) });
    await screen.findByTestId('valkyrie-console-page');

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

  it('shows pending reviews for the selected environment with an inbox link', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-pending-reviews');
    expect(panel).toHaveTextContent('Waiting on you');
    expect(within(panel).getByRole('link', { name: 'Open inbox' })).toHaveAttribute(
      'href',
      '/valkyrie/inbox',
    );
  });

  it('collapses overflowing pending reviews into a count', async () => {
    const pending = Array.from({ length: 5 }, (_, index) => ({
      itemId: `review:court_escalation:extra-${index}`,
      kind: 'court_escalation' as const,
      requestedAction: 'execute_action',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      title: `Action draft ${index}`,
      summary: 'needs a decision',
      audience: 'valkyrie',
      flockId: '',
      domain: 'k8s',
      riskClass: 'medium' as const,
      safetyClass: 'mutating',
      urgency: 0.5,
      requestedCapability: 'approve',
      evidence: {},
      status: 'pending' as const,
      requestedBy: 'odin-court',
      requestedAt: `2026-06-03T1${index}:00:00Z`,
      decidedBy: '',
      decidedAt: '',
      decisionReason: '',
      resolvedAt: '',
      applyOutcome: '',
      applyDetail: '',
    }));
    const reviews = {
      listReviews: async () => pending,
      getReview: async () => null,
      decideReview: () => Promise.reject(new Error('unused')),
      getSummary: async () => ({
        pendingTotal: 5,
        pendingByKind: {},
        pendingByRisk: {},
        countsByStatus: {},
      }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ 'valkyrie.reviews': reviews }) });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-pending-reviews');
    await waitFor(() => expect(panel).toHaveTextContent('and 2 more in the inbox'));
    expect(panel).toHaveTextContent('Action approval');
  });

  it('shows learned-skill activity and per-skill usage stats', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const strip = await screen.findByTestId('valkyrie-learned-activity');
    expect(strip).toHaveTextContent('Handled with a learned skill');

    const learning = screen.getByTestId('valkyrie-learning');
    await waitFor(() =>
      expect(
        within(learning).getByTestId('skill-stat-k8s_memory_pressure_probe'),
      ).toBeInTheDocument(),
    );
    expect(within(learning).getByTestId('skill-stat-k8s_memory_pressure_probe')).toHaveTextContent(
      'Used 7×',
    );
    expect(within(learning).getByTestId('skill-stat-node_cordon_drain')).toHaveTextContent(
      'rolled back',
    );
  });

  it('renders the investigation threshold from live severity config', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const authority = screen.getByTestId('valkyrie-authority');
    expect(authority).toHaveTextContent('investigation threshold');
    expect(authority).toHaveTextContent('Warning');
    expect(authority).toHaveTextContent('Critical');
    expect(authority).toHaveTextContent('Needs your approval');
  });

  it('shows the empty console when no valkyries have announced', async () => {
    const service = createMockValkyrieService();
    const empty = {
      ...service,
      getDashboard: async () => ({
        ...(await service.getDashboard()),
        valkyries: [],
      }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: empty }) });
    expect(await screen.findByTestId('valkyrie-console-empty')).toBeInTheDocument();
  });

  it('expands the timeline and filters it by kind', async () => {
    const user = userEvent.setup();
    const service = createMockValkyrieService();
    const seed = createSeedValkyrieDashboard();
    const manyEvents = Array.from({ length: 10 }, (_, index) => ({
      id: `event-${index}`,
      eventType: index % 2 === 0 ? 'signal.kubernetes.event' : 'valkyrie.judgment.proposed',
      kind: (index % 2 === 0 ? 'signal' : 'judgment') as 'signal' | 'judgment',
      environmentId: 'env-k8s-valhalla',
      summary: `event number ${index}`,
      observedAt: `2026-06-03T14:0${index % 10}:00Z`,
    }));
    const busy = {
      ...service,
      getDashboard: async () => ({
        ...seed,
        telemetry: { ...seed.telemetry!, recentEvents: manyEvents },
      }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: busy }) });
    await screen.findByTestId('valkyrie-console-page');

    const timeline = screen.getByTestId('valkyrie-signal-timeline');
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(7);

    await user.click(within(timeline).getByRole('button', { name: /show all 10 events/i }));
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(10);

    await user.selectOptions(within(timeline).getByLabelText('Filter timeline by kind'), [
      'judgment',
    ]);
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(5);

    await user.selectOptions(within(timeline).getByLabelText('Filter timeline by kind'), ['']);
    await user.click(within(timeline).getByRole('button', { name: /show fewer events/i }));
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(7);
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
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: failing }) });
    await screen.findByTestId('valkyrie-console-page');

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
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: paged }) });
    await screen.findByTestId('valkyrie-console-page');

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
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: oldBackend }) });
    await screen.findByTestId('valkyrie-console-page');

    const decisions = screen.getByTestId('valkyrie-decisions');
    await waitFor(() => expect(decisions).toHaveTextContent('does not serve decision history yet'));

    await user.click(screen.getByRole('button', { name: /browse observed signals/i }));
    const browser = await screen.findByTestId('valkyrie-signal-browser');
    await waitFor(() => expect(browser).toHaveTextContent('does not serve decision history yet'));
  });

  it('collapses repeated poll chatter in the timeline into one row', async () => {
    const service = createMockValkyrieService();
    const seed = createSeedValkyrieDashboard();
    const pollSpam = Array.from({ length: 6 }, (_, index) => ({
      id: `poll-${index}`,
      eventType: 'valkyrie.signal_poll.completed',
      kind: 'event' as const,
      environmentId: 'env-k8s-valhalla',
      summary: 'kubernetes-events poll collected 28 signal(s), published 0, enqueued 0',
      observedAt: `2026-06-03T14:0${index}:00Z`,
    }));
    const judgment = {
      id: 'judgment-1',
      eventType: 'valkyrie.judgment.proposed',
      kind: 'judgment' as const,
      environmentId: 'env-k8s-valhalla',
      summary: 'Idle triage: routine window',
      observedAt: '2026-06-03T13:59:00Z',
    };
    const chatty = {
      ...service,
      getDashboard: async () => ({
        ...seed,
        telemetry: { ...seed.telemetry!, recentEvents: [...pollSpam, judgment] },
      }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: chatty }) });
    await screen.findByTestId('valkyrie-console-page');

    const timeline = screen.getByTestId('valkyrie-signal-timeline');
    // Six identical polls collapse to one row with a repeat badge; the
    // judgment stays visible instead of being pushed out.
    expect(within(timeline).getAllByRole('listitem')).toHaveLength(2);
    expect(timeline).toHaveTextContent('×6');
    expect(timeline).toHaveTextContent('Idle triage: routine window');
  });

  it('counts observed signals from live poll telemetry when idle', async () => {
    const service = createMockValkyrieService();
    const seed = createSeedValkyrieDashboard();
    seed.environments = seed.environments.map((entry) =>
      entry.id === 'env-k8s-valhalla' ? { ...entry, signalCount: 0 } : entry,
    );
    const idle = {
      ...service,
      getDashboard: async () => seed,
      listDecisions: async () => ({ items: [], total: 0, limit: 8, offset: 0 }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: idle }) });
    await screen.findByTestId('valkyrie-console-page');

    const situation = screen.getByTestId('valkyrie-current-situation');
    // env summary says 0, but byEnvironment telemetry collected 36 signals.
    await waitFor(() => expect(situation).toHaveTextContent('36 signal(s) observed'));
  });

  it('explains an idle environment instead of showing an empty situation', async () => {
    const service = createMockValkyrieService();
    const idle = {
      ...service,
      listDecisions: async () => ({ items: [], total: 0, limit: 8, offset: 0 }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: idle }) });
    await screen.findByTestId('valkyrie-console-page');

    const situation = screen.getByTestId('valkyrie-current-situation');
    await waitFor(() => expect(situation).toHaveTextContent('none crossed the task threshold'));
    expect(situation).toHaveTextContent('warning or critical');
  });

  it('lists the learned skills adopted on the environment', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-learned-skills');
    await waitFor(() =>
      expect(
        within(panel).getByTestId('learned-skill-k8s_memory_pressure_probe'),
      ).toBeInTheDocument(),
    );
    expect(panel).toHaveTextContent('Read-only probe that inspects');
    expect(panel).toHaveTextContent('adopted');
    expect(within(panel).getByTestId('learned-skill-registry_token_refresh_check')).toBeVisible();
  });

  it('opens the skill viewer from the learned skills list', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-learned-skills');
    await user.click(await within(panel).findByTestId('learned-skill-k8s_memory_pressure_probe'));

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('def run(signal: dict)'));
    expect(dialog).toHaveTextContent('kubernetes>=29.0.0');
  });

  it('makes a learned-skill judgment reference clickable and opens the viewer', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    // decision-oom-1 carries evidence skill_name: k8s_memory_pressure_probe
    const strip = await screen.findByTestId('valkyrie-learned-activity');
    await user.click(await within(strip).findByTestId('skill-link-k8s_memory_pressure_probe'));

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('Read-only probe that inspects'));

    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('links the skill inside an expanded decision detail', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

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

  it('says loudly when the backend has no skills endpoint yet', async () => {
    const broken = {
      listSkills: () => Promise.reject(new Error('404 Not Found')),
      getSkill: () => Promise.reject(new Error('404 Not Found')),
    };
    render(<ValkyrieConsolePage />, {
      wrapper: wrapWithValkyrie({ 'valkyrie.skills': broken }),
    });
    await screen.findByTestId('valkyrie-console-page');

    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-skills-unavailable')).toHaveTextContent(
        'does not serve the skills endpoint yet',
      ),
    );
  });

  it('shows an empty state when no skills are adopted', async () => {
    const empty = {
      listSkills: async () => [],
      getSkill: async () => null,
    };
    render(<ValkyrieConsolePage />, {
      wrapper: wrapWithValkyrie({ 'valkyrie.skills': empty }),
    });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-learned-skills');
    await waitFor(() =>
      expect(panel).toHaveTextContent('No learned skills adopted on this environment yet'),
    );
  });

  // -------------------------------------------------------------------------
  // Realm build governance — the realm's build grant lives on the console.
  // -------------------------------------------------------------------------

  it('shows the effective autonomy from the realm build grant, configured as secondary', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    // Sigrun on env-k8s-valhalla → realm `valhalla` whose build grant is level 2.
    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-effective-autonomy')).toHaveTextContent(
        'Autonomous · effective (realm grant level 2)',
      ),
    );
    expect(screen.getByTestId('valkyrie-configured-autonomy')).toHaveTextContent(
      'configured autonomous',
    );
    expect(screen.getByTestId('valkyrie-autonomy-provenance')).toHaveTextContent(
      'realm grant level 2 on valhalla',
    );
  });

  it('lets the realm grant override a guarded configured mode', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    // Runa is configured guarded but shares realm `valhalla`: the grant wins.
    await user.click(screen.getByRole('button', { name: /Runa/ }));

    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-effective-autonomy')).toHaveTextContent(
        'Autonomous · effective (realm grant level 2)',
      ),
    );
    expect(screen.getByTestId('valkyrie-configured-autonomy')).toHaveTextContent(
      'configured guarded',
    );
  });

  it('falls back to the configured mode when the realm has no build grant', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    // Saga lives on env-host-jozef → realm `host-jozef` with no grants.
    await user.click(screen.getByRole('button', { name: /Saga/ }));

    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-effective-autonomy')).toHaveTextContent(
        'Guarded · configured (no realm build grant)',
      ),
    );
    expect(screen.queryByTestId('valkyrie-configured-autonomy')).toBeNull();
    expect(screen.getByTestId('valkyrie-autonomy-provenance')).toHaveTextContent(
      'No build grant on realm host-jozef yet',
    );
    expect(await screen.findByTestId('tool-builder-ungranted')).toBeInTheDocument();
  });

  it('never fetches trust grants for an environment without a realm', async () => {
    const user = userEvent.setup();
    const service = createMockRealmGovernanceService({
      // Only valhalla exists as a realm; host-jozef and printer-forge do not.
      realms: createSeedRealms().filter((realm) => realm.slug === 'valhalla'),
    });
    const grantsSpy = vi.spyOn(service, 'listTrustGrants');
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ 'valkyrie.realms': service }) });
    await screen.findByTestId('valkyrie-console-page');

    // Sigrun's realm exists → the grants request fires exactly for valhalla.
    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-effective-autonomy')).toHaveTextContent(
        'effective (realm grant level 2)',
      ),
    );

    // Saga's environment has no realm → no request, and the panel says why.
    await user.click(screen.getByRole('button', { name: /Saga/ }));
    expect(await screen.findByTestId('valkyrie-realm-unconfigured')).toHaveTextContent(
      'No realm is configured for host-jozef',
    );
    expect(screen.queryByTestId('tool-builder-card')).toBeNull();
    // The badge stays on the configured mode without claiming effectiveness.
    expect(screen.getByTestId('valkyrie-effective-autonomy')).toHaveTextContent('Guarded');
    expect(grantsSpy).toHaveBeenCalledWith('valhalla');
    expect(grantsSpy).not.toHaveBeenCalledWith('host-jozef');
  });

  it('renders the grant card in the authority panel and saving updates the badge', async () => {
    const user = userEvent.setup();
    const service = createMockRealmGovernanceService();
    const createSpy = vi.spyOn(service, 'createTrustGrant');
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ 'valkyrie.realms': service }) });
    await screen.findByTestId('valkyrie-console-page');

    const authority = screen.getByTestId('valkyrie-authority');
    expect(authority).toHaveTextContent('realm build governance — valhalla');
    const card = await within(authority).findByTestId('tool-builder-card');
    // The card is addressed by the environment's display name.
    await user.selectOptions(
      within(card).getByLabelText('Build trust level for Valhalla k8s'),
      '4',
    );
    await user.click(within(card).getByTestId('tool-builder-save'));

    expect(createSpy).toHaveBeenCalledWith('valhalla', {
      action_class: 'build',
      target: '*',
      level: 4,
      limits: { workflow: 'valkyrie-tool-forge' },
      granted_by: 'human:operator',
    });
    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-effective-autonomy')).toHaveTextContent(
        'Yolo · effective (realm grant level 4)',
      ),
    );
  });

  it('keeps the badge on the configured mode when realm governance is unavailable', async () => {
    const broken = {
      ...createMockRealmGovernanceService(),
      listTrustGrants: () => Promise.reject(new Error('realms offline')),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ 'valkyrie.realms': broken }) });
    await screen.findByTestId('valkyrie-console-page');

    const authority = screen.getByTestId('valkyrie-authority');
    await waitFor(() =>
      expect(within(authority).getByTestId('tool-builder-error')).toHaveTextContent(
        'realms offline',
      ),
    );
    const badge = screen.getByTestId('valkyrie-effective-autonomy');
    expect(badge).toHaveTextContent('Autonomous');
    expect(badge).not.toHaveTextContent('effective');
    expect(badge).not.toHaveTextContent('configured');
    expect(screen.queryByTestId('valkyrie-autonomy-provenance')).toBeNull();
  });
});

describe('hero actions', () => {
  it('shows identity chips (env, kind, flock, realm) and rich stat cards', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    const hero = await screen.findByTestId('valkyrie-console-hero');

    expect(hero).toHaveTextContent('env Valhalla k8s');
    expect(hero).toHaveTextContent('Kubernetes');
    expect(hero).toHaveTextContent('flock Kubernetes Valkyries');
    expect(hero).toHaveTextContent('realm valhalla');
    // Operational health folds env health, resident status, and confidence.
    expect(hero).toHaveTextContent('watch · busy');
    expect(hero).toHaveTextContent('judgment confidence 86%');
    // Wakefulness explains itself.
    expect(hero).toHaveTextContent('Actively working on something.');
  });

  it('joins and leaves the environment huddle', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const button = screen.getByTestId('valkyrie-join-huddle');
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent('Join huddle');

    await user.click(button);
    await waitFor(() => expect(button).toHaveTextContent('Leave huddle'));

    await user.click(button);
    await waitFor(() => expect(button).toHaveTextContent('Join huddle'));
  });

  it('sends a direct message to the resident after joining the huddle', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    // Messaging mirrors the backend contract: join the huddle first.
    expect(screen.getByTestId('valkyrie-direct-message')).toBeDisabled();
    await user.click(screen.getByTestId('valkyrie-join-huddle'));
    await waitFor(() => expect(screen.getByTestId('valkyrie-direct-message')).toBeEnabled());

    await user.click(screen.getByTestId('valkyrie-direct-message'));
    const composer = await screen.findByTestId('valkyrie-dm-composer');
    const input = within(composer).getByLabelText('Direct message to Sigrun');

    // Empty body cannot be sent.
    expect(screen.getByTestId('valkyrie-dm-send')).toBeDisabled();
    await user.type(input, 'Please summarize the registry incident.');
    await user.click(screen.getByTestId('valkyrie-dm-send'));

    expect(await screen.findByTestId('valkyrie-dm-sent')).toHaveTextContent('Delivered to Sigrun.');
    expect(input).toHaveValue('');
  });

  it('disables huddle actions when the environment has no open huddle', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    // Saga's host environment has no huddle in the seed.
    await user.click(screen.getByRole('button', { name: /Saga/ }));
    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-console-hero')).toHaveTextContent('valkyrie:Saga'),
    );
    expect(screen.getByTestId('valkyrie-join-huddle')).toBeDisabled();
    expect(screen.getByTestId('valkyrie-direct-message')).toBeDisabled();
  });

  it('changes autonomy from the Change autonomy panel', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    expect(screen.queryByLabelText('Autonomy mode for Sigrun')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('valkyrie-change-autonomy'));
    const select = await screen.findByLabelText('Autonomy mode for Sigrun');

    await user.selectOptions(select, ['guarded']);
    await waitFor(() => expect(select).toHaveValue('guarded'));
  });
});
