import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import {
  createMockRealmGovernanceService,
  createMockValkyrieService,
  createSeedRealms,
  createSeedValkyrieDashboard,
} from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { DreamPanel, ValkyrieConsolePage } from './ValkyrieConsolePage';

describe('ValkyrieConsolePage', () => {
  it('shows the loading state first', () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    expect(screen.getByTestId('valkyrie-console-loading')).toBeInTheDocument();
  });

  it('renders the roster, the resident, and its evolution', async () => {
    const seed = createSeedValkyrieDashboard();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-console-page')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-roster')).toHaveTextContent(seed.valkyries[0]!.name);
    expect(screen.getByTestId('valkyrie-console-hero')).toHaveTextContent(seed.valkyries[0]!.name);
    expect(screen.getByTestId('valkyrie-evolution')).toBeInTheDocument();
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
    expect(screen.getByTestId('valkyrie-authority')).toHaveTextContent(
      'Acts on its own within its authority boundary',
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
        pendingByEnvironment: {},
        countsByStatus: {},
      }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ 'valkyrie.reviews': reviews }) });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-pending-reviews');
    await waitFor(() => expect(panel).toHaveTextContent('and 2 more in the inbox'));
    expect(panel).toHaveTextContent('Action approval');
  });

  it('renders upgraded learning cards with lifecycle, confidence, and feedback chips', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-learning');
    const card = within(panel).getByTestId('learning-card-learn-k8s-oom-canary');
    expect(card).toHaveTextContent('OOMKilled with rising queue depth');
    expect(card).toHaveTextContent('canary');
    expect(card).toHaveTextContent('flock');
    expect(card).toHaveTextContent('81%');
    expect(within(card).getByTestId('learning-repetition-learn-k8s-oom-canary')).toHaveTextContent(
      '× 3 seen',
    );
    expect(within(card).getByTestId('learning-fb-learn-k8s-oom-canary')).toHaveTextContent(
      'fb: awaiting',
    );
    expect(card).toHaveTextContent('valkyrie-valhalla-runa · env-k8s-valhalla');
    expect(card).toHaveTextContent('3/3 replayed incidents');
    expect(card).toHaveTextContent('open');

    // No repetition chip for a once-seen learning.
    const rollback = within(panel).getByTestId('learning-card-learn-k8s-eviction-rollback');
    expect(
      within(rollback).queryByTestId('learning-repetition-learn-k8s-eviction-rollback'),
    ).toBeNull();
    expect(rollback).toHaveTextContent('rolled back');
  });

  it('filters learning cards by scope chips with live counts', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const panel = screen.getByTestId('valkyrie-learning');
    const filters = within(panel).getByTestId('learning-scope-filters');
    // Sigrun's environment holds two flock-scoped learnings and nothing else.
    expect(within(filters).getByTestId('learning-scope-filter-flock')).toHaveTextContent('flock 2');
    expect(within(filters).getByTestId('learning-scope-filter-private')).toHaveTextContent(
      'private 0',
    );

    await user.click(within(filters).getByTestId('learning-scope-filter-private'));
    expect(within(panel).queryByTestId('learning-card-learn-k8s-oom-canary')).toBeNull();

    // Toggling the same chip off restores the unfiltered list.
    await user.click(within(filters).getByTestId('learning-scope-filter-private'));
    expect(within(panel).getByTestId('learning-card-learn-k8s-oom-canary')).toBeInTheDocument();
  });

  it('opens the learning viewer from a learning card', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    await user.click(screen.getByTestId('learning-card-learn-k8s-oom-canary'));

    const viewer = await screen.findByTestId('learning-viewer');
    await waitFor(() => expect(viewer).toHaveTextContent('learn-k8s-oom-canary'));
    expect(viewer).toHaveTextContent('structured payload');
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

    // env summary says 0, but byEnvironment telemetry collected 36 signals.
    await waitFor(() =>
      expect(screen.getByTestId('valkyrie-now')).toHaveTextContent('36 signal(s) observed'),
    );
  });

  it('explains an idle environment instead of showing an empty situation', async () => {
    const service = createMockValkyrieService();
    const idle = {
      ...service,
      listDecisions: async () => ({ items: [], total: 0, limit: 8, offset: 0 }),
    };
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: idle }) });
    await screen.findByTestId('valkyrie-console-page');

    const now = screen.getByTestId('valkyrie-now');
    await waitFor(() => expect(now).toHaveTextContent('none crossed the task threshold'));
    expect(now).toHaveTextContent('warning or critical');
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
    expect(panel).toHaveTextContent('installed');
    expect(panel).toHaveTextContent('recorded use');
    expect(panel).toHaveTextContent('no recorded use');
    await userEvent.setup().click(screen.getByTestId('evolution-fold-tools'));
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
      expect(panel).toHaveTextContent('No learned skills installed on this environment yet'),
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
  it('says who the resident is in one line and keeps the rest with its permissions', async () => {
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    const hero = await screen.findByTestId('valkyrie-console-hero');

    expect(hero).toHaveTextContent('Keeps Valhalla k8s.');
    expect(hero).toHaveTextContent('watch');
    const identity = screen.getByTestId('valkyrie-identity');
    expect(identity).toHaveTextContent('Kubernetes');
    expect(identity).toHaveTextContent('flock Kubernetes Valkyries');
    expect(identity).toHaveTextContent('realm valhalla');
    expect(identity).toHaveTextContent('judgment confidence 86%');
  });

  it('joins the huddle when the operator messages, and can leave it', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    await user.click(screen.getByTestId('valkyrie-direct-message'));
    expect(await screen.findByTestId('valkyrie-dm-composer')).toBeInTheDocument();

    await user.click(screen.getByTestId('valkyrie-leave-huddle'));
    await waitFor(() =>
      expect(screen.queryByTestId('valkyrie-dm-composer')).not.toBeInTheDocument(),
    );
  });

  it('sends a direct message to the resident after joining the huddle', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

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
      expect(screen.getByTestId('valkyrie-console-hero')).toHaveTextContent('Saga'),
    );
    expect(screen.getByTestId('valkyrie-direct-message')).toBeDisabled();
  });

  it('changes autonomy from the Change autonomy panel', async () => {
    const user = userEvent.setup();
    render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie() });
    await screen.findByTestId('valkyrie-console-page');

    const select = await screen.findByLabelText('Autonomy mode for Sigrun');

    await user.selectOptions(select, ['guarded']);
    await waitFor(() => expect(select).toHaveValue('guarded'));
  });
});

it('shows an autonomy update failure instead of silently resetting the selector', async () => {
  const user = userEvent.setup();
  const service = createMockValkyrieService();
  service.updateAutonomy = async () => {
    throw new Error('Valkyrie not found');
  };
  render(<ValkyrieConsolePage />, { wrapper: wrapWithValkyrie({ valkyrie: service }) });
  await screen.findByTestId('valkyrie-console-page');
  await user.selectOptions(screen.getByLabelText('Autonomy mode for Sigrun'), ['guarded']);
  expect(await screen.findByRole('alert')).toHaveTextContent('Valkyrie not found');
});

describe('DreamPanel', () => {
  const seed = createSeedValkyrieDashboard();
  const byId = (id: string) => seed.valkyries.find((entry) => entry.id === id)!;

  it('summarises the last dream and lists what it retired and merged', () => {
    render(<DreamPanel valkyrie={byId('valkyrie-valhalla-sigrun')} />);

    const panel = screen.getByTestId('valkyrie-last-dream');
    expect(screen.getByTestId('dream-status')).toHaveTextContent(/^Completed · \d+[smhd] ago$/);
    expect(panel).toHaveTextContent('1m 35s');
    expect(panel).toHaveTextContent('1 promoted · 1 stale');
    expect(panel).toHaveTextContent('2 merged of 38 reviewed');
    expect(panel).toHaveTextContent('1 superseded · 1 debug leftover · 1 unused');
    expect(panel).toHaveTextContent('1 held promotions · 1 improvements');

    const retired = screen.getByTestId('dream-retired-tools');
    expect(retired).toHaveTextContent('Learned tools retired · 3');
    expect(retired).toHaveTextContent('k8s_quantity_parse_v1');
    expect(retired).toHaveTextContent('replaced by k8s_quantity_parse');
    expect(retired).toHaveTextContent('scratch probe from an investigation');
    expect(screen.getByTestId('dream-lessons-merged')).toHaveTextContent(
      'lessons/oom-restart-loop.md',
    );
    expect(screen.getByTestId('dream-lessons-superseded')).toHaveTextContent(
      'lessons/evict-on-pressure.md',
    );
    expect(screen.getByTestId('dream-skills-stale')).toHaveTextContent('legacy-node-drain');
    expect(screen.getByTestId('dream-promotions-held')).toHaveTextContent('noisy-probe-mute');
    expect(screen.getByTestId('dream-improvement-actions')).toHaveTextContent(
      'commission_tool:friction-node-pressure',
    );
    // Empty lists are not rendered as empty sections.
    expect(screen.queryByTestId('dream-lessons-pruned')).not.toBeInTheDocument();
    expect(screen.queryByTestId('dream-restored-tools')).not.toBeInTheDocument();
    expect(screen.queryByTestId('dream-error')).not.toBeInTheDocument();
  });

  it('shows a failed dream as failed, with its error', () => {
    render(<DreamPanel valkyrie={byId('valkyrie-printer-eir')} />);

    expect(screen.getByTestId('dream-status')).toHaveTextContent(/^Failed/);
    expect(screen.getByTestId('dream-status')).toHaveClass('niuu:text-critical');
    expect(screen.getByRole('alert')).toHaveTextContent(
      'MimirUnavailable: 503 from mimir-shared while listing lessons',
    );
    const panel = screen.getByTestId('valkyrie-last-dream');
    expect(panel).toHaveTextContent('not due');
    expect(panel).toHaveTextContent('not curated');
    expect(panel).toHaveTextContent('no learned-tool curator on this resident');
  });

  it('says so when a failed dream reported no error text', () => {
    const eir = byId('valkyrie-printer-eir');
    render(<DreamPanel valkyrie={{ ...eir, lastDream: { ...eir.lastDream!, error: '' } }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('failed without reporting an error');
  });

  it('says how much lesson curation left for the next dream', () => {
    const sigrun = byId('valkyrie-valhalla-sigrun');
    const dream = sigrun.lastDream!;
    render(
      <DreamPanel
        valkyrie={{
          ...sigrun,
          lastDream: { ...dream, lessons: { ...dream.lessons!, remaining: 127 } },
        }}
      />,
    );
    expect(screen.getByTestId('valkyrie-last-dream')).toHaveTextContent(
      '2 merged of 38 reviewed · 127 lessons left for the next dream',
    );
  });

  it('notes a curator run that retired nothing', () => {
    const sigrun = byId('valkyrie-valhalla-sigrun');
    const dream = sigrun.lastDream!;
    render(
      <DreamPanel
        valkyrie={{
          ...sigrun,
          lastDream: {
            ...dream,
            learnedTools: { ...dream.learnedTools!, retired: [], retiredByReason: {} },
          },
        }}
      />,
    );
    expect(screen.getByTestId('valkyrie-last-dream')).toHaveTextContent(
      '19 installed, none retired',
    );
    expect(screen.queryByTestId('dream-retired-tools')).not.toBeInTheDocument();
  });

  it('explains a resident that has not reported a finished dream', () => {
    const { rerender } = render(<DreamPanel valkyrie={byId('valkyrie-host-email')} />);
    expect(screen.getByTestId('valkyrie-last-dream')).toHaveTextContent(
      'No finished consolidation dream has been reported by this resident yet.',
    );
    expect(screen.queryByTestId('dream-status')).not.toBeInTheDocument();

    rerender(<DreamPanel valkyrie={{ ...byId('valkyrie-host-email'), wakefulness: 'dreaming' }} />);
    expect(screen.getByTestId('valkyrie-last-dream')).toHaveTextContent(
      'Dreaming now — no earlier dream has been reported.',
    );
  });
});

describe('what needs the operator', () => {
  it('says nothing needs them when nothing does', async () => {
    const reviews = {
      listReviews: async () => [],
      getReview: async () => null,
      decideReview: () => Promise.reject(new Error('unused')),
      getSummary: async () => ({ pending: 0, oldestPendingAt: null, byKind: {} }),
    };
    render(<ValkyrieConsolePage />, {
      wrapper: wrapWithValkyrie({ reviews } as never),
    });
    await screen.findByTestId('valkyrie-console-page');

    expect(await screen.findByText('Nothing needs you right now.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'See activity' })).toHaveAttribute(
      'href',
      '/valkyrie/activity?environment=env-k8s-valhalla',
    );
  });

  it('puts a failing tool and a failed dream first', async () => {
    const seed = createSeedValkyrieDashboard();
    const resident = seed.valkyries[0]!;
    resident.lastDream = {
      ...resident.lastDream!,
      status: 'failed',
      error: 'TimeoutError: slow',
      evolution: {
        ...resident.lastDream!.evolution!,
        attention: [{ tool: 'list_pods_for_job', runs: 33, lastUsedAt: '' }],
      },
    };
    render(<ValkyrieConsolePage />, {
      wrapper: wrapWithValkyrie({ valkyrie: createMockValkyrieService(seed) }),
    });
    await screen.findByTestId('valkyrie-console-page');

    const needs = screen.getByTestId('valkyrie-needs-you');
    expect(within(needs).getByTestId('evolution-attention')).toHaveTextContent(
      'list_pods_for_job failed all 33 runs and is still installed.',
    );
    expect(within(needs).getByTestId('evolution-dream-failed')).toHaveTextContent(
      'TimeoutError: slow',
    );
  });
});
