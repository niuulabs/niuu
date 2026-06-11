import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ValkyriePage } from './ValkyriePage';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import {
  createMockValkyrieService,
  createMockValkyrieSignalStream,
  createSeedValkyrieDashboard,
} from '../adapters/mock';

function createDemoDashboard() {
  const dashboard = createSeedValkyrieDashboard();
  return {
    ...dashboard,
    telemetry: dashboard.telemetry
      ? { ...dashboard.telemetry, source: 'demo_projection', verified: false }
      : dashboard.telemetry,
  };
}

describe('ValkyriePage', () => {
  it('renders environment, flock, signal, state, huddle, and learning panels', async () => {
    render(<ValkyriePage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: createMockValkyrieService(createDemoDashboard()),
      }),
    });

    expect(await screen.findByTestId('valkyrie-page')).toBeInTheDocument();
    expect(screen.getByTestId('environment-env-k8s-valhalla')).toBeInTheDocument();
    expect(screen.getByTestId('flock-flock-k8s')).toBeInTheDocument();
    expect(screen.getByTestId('signal-panel')).toBeInTheDocument();
    expect(screen.getByTestId('environment-state-panel')).toBeInTheDocument();
    expect(screen.getByTestId('huddle-panel')).toBeInTheDocument();
    expect(screen.getByTestId('learning-panel')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-telemetry-panel')).toHaveTextContent(
      'Operational telemetry',
    );
    expect(screen.getByTestId('valkyrie-telemetry-panel')).toHaveTextContent('unverified');
    expect(screen.getByTestId('valkyrie-telemetry-panel')).toHaveTextContent(
      'Qwen/Qwen3.6-35B-A3B-FP8',
    );
    expect(screen.getByTestId('live-k8s-valkyries')).toHaveTextContent('Thinking');
    expect(screen.getByTestId('live-k8s-valkyries')).toHaveTextContent('Conclusions');
    expect(screen.getByTestId('valkyrie-live-conclusions')).toHaveTextContent(
      'Persistent ImagePullBackOff',
    );
    expect(screen.getByTestId('valkyrie-telemetry-panel')).toHaveTextContent('Recent tasks');
    expect(screen.getByTestId('valkyrie-telemetry-panel')).toHaveTextContent('queue_full');
    expect(screen.getByTestId('flock-live-report')).toHaveTextContent('K8s flock routing');
  });

  it('renders verified telemetry without seeded projection panels', async () => {
    render(<ValkyriePage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-live-console')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Valhalla k8s' })).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-live-scope-rail')).toHaveTextContent('All Valkyries');
    expect(screen.getByTestId('valkyrie-live-metrics')).toHaveTextContent('Open signals');
    expect(screen.getByTestId('valkyrie-live-metrics')).toHaveTextContent('Learning in test');
    expect(screen.getByTestId('valkyrie-event-log')).toHaveTextContent('Live event and log tail');
    expect(screen.getByTestId('valkyrie-event-log')).toHaveTextContent(
      'Prepare a guarded rollout fix',
    );
    expect(screen.getByTestId('valkyrie-court-panel')).toHaveTextContent('ODIN court');
    expect(screen.getByTestId('valkyrie-actions-panel')).toHaveTextContent(
      'prepare_rollout_remediation',
    );
    expect(screen.getByTestId('valkyrie-work-queue')).toHaveTextContent('Pod checkout/api');
    expect(screen.getByTestId('valkyrie-llm-status')).toHaveTextContent('Qwen/Qwen3.6-35B-A3B-FP8');
    expect(screen.getByTestId('valkyrie-live-runtime')).toHaveTextContent('Sigrun');
    expect(screen.getByTestId('valkyrie-live-runtime')).toHaveTextContent(
      'Evidence-first cluster guardian',
    );
    expect(screen.queryByTestId('valkyrie-telemetry-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('signal-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('huddle-panel')).not.toBeInTheDocument();
    expect(screen.getByText('Printer forge')).toBeInTheDocument();
    expect(screen.getAllByText(/configured only/i).length).toBeGreaterThan(0);
    expect(
      screen.queryByText('External sender asks for review before Friday'),
    ).not.toBeInTheDocument();
  });

  it('renders empty verified telemetry without falling back to seeded projections', async () => {
    const user = userEvent.setup();
    const dashboard = createSeedValkyrieDashboard();
    const telemetry = dashboard.telemetry!;
    const quietDashboard = {
      ...dashboard,
      telemetry: {
        ...telemetry,
        source: '',
        verified: true,
        lastObservedAt: 'not-a-date',
        byEnvironment: [],
        recentPolls: [],
        recentTasks: [],
        recentOutcomes: [],
        recentEvents: [],
        recentLogs: [],
        recentToolNeeds: [],
        recentLearning: [],
        runtime: [],
        gaps: [],
        totals: {
          ...telemetry.totals,
          eventsObserved: 0,
          rawSignalEvents: 0,
          signalsCollected: 0,
          signalsPublished: 0,
          tasksEnqueued: 0,
          tasksStarted: 0,
          tasksCompleted: 0,
          tasksFailed: 0,
          tasksDropped: 0,
          judgments: 0,
          actions: 0,
          learningEvents: 0,
          dreamCyclesStarted: 0,
          dreamCyclesCompleted: 0,
          dreamCyclesNoop: 0,
          dreamCyclesFailed: 0,
          skillProposals: 0,
          toolRequests: 0,
          llmCalls: 0,
          llmTokens: 0,
        },
        llm: {
          ...telemetry.llm,
          model: '',
          reflectionModel: '',
          status: 'idle',
          postSessionReflectionEnabled: false,
        },
      },
    };

    const wrapper = wrapWithValkyrie({
      valkyrie: createMockValkyrieService(quietDashboard),
    });
    const { unmount } = render(<ValkyriePage />, {
      wrapper,
    });

    expect(await screen.findByTestId('valkyrie-live-console')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-live-metrics')).toHaveTextContent('0');
    expect(screen.getByTestId('valkyrie-live-runtime')).toHaveTextContent(
      'No runtime starts observed',
    );
    expect(screen.getByTestId('valkyrie-work-queue')).toHaveTextContent('No task telemetry');
    expect(screen.getByTestId('valkyrie-event-log')).toHaveTextContent(
      'No Valkyrie events or structured logs observed',
    );
    expect(screen.getByTestId('valkyrie-evolution-loop')).toHaveTextContent('stalled');
    expect(screen.getByTestId('valkyrie-evolution-loop')).toHaveTextContent(
      'No dream cycle has been observed for this window.',
    );
    expect(screen.getByTestId('valkyrie-evolution-loop')).toHaveTextContent(
      'No capability gaps in this scope',
    );
    expect(screen.getByTestId('valkyrie-gaps')).toHaveTextContent(
      'No telemetry gaps in the current window',
    );
    expect(screen.getByTestId('valkyrie-llm-status')).toHaveTextContent('unknown');
    expect(screen.getByTestId('valkyrie-llm-status')).toHaveTextContent('reflection off');
    expect(screen.getByTestId('valkyrie-live-scope-rail')).toHaveTextContent('0');

    await user.click(screen.getByRole('button', { name: /all valkyries/i }));

    expect(screen.getByRole('heading', { level: 1, name: 'All Valkyries' })).toBeInTheDocument();

    unmount();
    render(<ValkyriePage defaultView="topology" />, {
      wrapper: wrapWithValkyrie({
        valkyrie: createMockValkyrieService(quietDashboard),
      }),
    });

    expect(await screen.findByTestId('valkyrie-live-console')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-live-environments')).toHaveTextContent(
      'No live environments observed',
    );
  });

  it('renders verified route views from live telemetry', async () => {
    const { rerender } = render(<ValkyriePage defaultView="topology" />, {
      wrapper: wrapWithValkyrie(),
    });

    expect(await screen.findByTestId('valkyrie-topology-view')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-live-environments')).toHaveTextContent('env-k8s-valhalla');

    rerender(<ValkyriePage defaultView="lineage" />);
    expect(await screen.findByTestId('valkyrie-lineage-view')).toHaveTextContent(
      'Prepare a guarded rollout fix',
    );

    rerender(<ValkyriePage defaultView="learning" />);
    expect(await screen.findByTestId('valkyrie-learning-exchange')).toHaveTextContent(
      'Flock learning exchange',
    );
    expect(screen.getByTestId('valkyrie-learning-exchange')).toHaveTextContent(
      'OOMKilled with rising queue depth',
    );
    expect(screen.getByTestId('valkyrie-learning-exchange')).toHaveTextContent('rolled back');
    expect(screen.getAllByRole('button', { name: /adopt/i }).length).toBeGreaterThan(0);

    rerender(<ValkyriePage defaultView="huddles" />);
    expect(await screen.findByTestId('valkyrie-huddles-view')).toHaveTextContent(
      'No verified huddle messages',
    );
  });

  it('switches from environment learning to flock learning', async () => {
    const user = userEvent.setup();
    render(<ValkyriePage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: createMockValkyrieService(createDemoDashboard()),
      }),
    });

    await user.click(await screen.findByTestId('flock-flock-k8s'));

    expect(
      screen.getByRole('heading', { level: 1, name: 'Kubernetes Valkyries' }),
    ).toBeInTheDocument();
    expect(screen.getByText('OOMKilled with rising queue depth')).toBeInTheDocument();
  });

  it('lets an operator approve learning exchange artifacts', async () => {
    const user = userEvent.setup();
    const service = createMockValkyrieService();
    const adoptLearning = vi.spyOn(service, 'adoptLearning');
    render(<ValkyriePage defaultView="learning" />, {
      wrapper: wrapWithValkyrie({ valkyrie: service }),
    });

    expect(await screen.findByTestId('valkyrie-learning-exchange')).toHaveTextContent(
      'Vendor deadline language',
    );
    await user.click(screen.getByRole('button', { name: 'candidate 1' }));
    expect(screen.getByTestId('valkyrie-learning-exchange')).toHaveTextContent(
      'Vendor deadline language',
    );

    await user.click(screen.getByRole('button', { name: 'adopt' }));

    await waitFor(() =>
      expect(adoptLearning).toHaveBeenCalledWith(
        expect.objectContaining({ learningId: 'learn-email-vendor-escalation' }),
      ),
    );
  });

  it('opens a learning review drawer with real lifecycle controls', async () => {
    const user = userEvent.setup();
    const service = createMockValkyrieService();
    const promoteLearning = vi.spyOn(service, 'promoteLearning');
    const demoteLearning = vi.spyOn(service, 'demoteLearning');
    const rollbackLearning = vi.spyOn(service, 'rollbackLearning');
    render(<ValkyriePage defaultView="learning" />, {
      wrapper: wrapWithValkyrie({ valkyrie: service }),
    });

    const reviewButtons = await screen.findAllByRole('button', { name: 'review' });
    await user.click(reviewButtons[0]!);
    const dialog = await screen.findByRole('dialog', {
      name: 'Review OOMKilled with rising queue depth',
    });

    expect(within(dialog).getByText('Generated artifact')).toBeInTheDocument();
    expect(
      within(dialog).getByText(/capability: inspect.kubernetes.pod.oomkilled/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('Odin review')).toBeInTheDocument();
    expect(within(dialog).getByText('active in runtime')).toBeInTheDocument();
    expect(within(dialog).getByText('artifact ravn skill tool')).toBeInTheDocument();

    await user.click(within(dialog).getByRole('button', { name: 'promote' }));
    await user.click(within(dialog).getByRole('button', { name: 'demote' }));
    await user.click(within(dialog).getByRole('button', { name: 'rollback' }));

    await waitFor(() =>
      expect(promoteLearning).toHaveBeenCalledWith(
        expect.objectContaining({
          learningId: 'learn-k8s-oom-canary',
          targetScope: 'shared',
        }),
      ),
    );
    await waitFor(() =>
      expect(demoteLearning).toHaveBeenCalledWith(
        expect.objectContaining({
          learningId: 'learn-k8s-oom-canary',
          targetScope: 'domain',
        }),
      ),
    );
    await waitFor(() =>
      expect(rollbackLearning).toHaveBeenCalledWith(
        expect.objectContaining({ learningId: 'learn-k8s-oom-canary' }),
      ),
    );
  });

  it('lets an operator join and speak in a huddle', async () => {
    const user = userEvent.setup();
    const service = createMockValkyrieService(createDemoDashboard());
    const joinHuddle = vi.spyOn(service, 'joinHuddle');
    const sendHuddleMessage = vi.spyOn(service, 'sendHuddleMessage');
    render(<ValkyriePage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: service,
      }),
    });

    await user.type(
      await screen.findByLabelText(/participant for valhalla memory/i),
      'human:jozef',
    );
    await user.selectOptions(screen.getByLabelText(/action for valhalla memory/i), 'teach');
    await user.click(await screen.findByRole('button', { name: 'Join' }));
    await user.type(screen.getByLabelText(/message valhalla memory/i), 'What is blocked?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() =>
      expect(joinHuddle).toHaveBeenCalledWith({
        huddleId: 'huddle-valhalla-now',
        participantId: 'human:jozef',
        displayName: 'human:jozef',
        action: 'teach',
        targetFlockId: 'flock-k8s',
      }),
    );
    await waitFor(() =>
      expect(sendHuddleMessage).toHaveBeenCalledWith({
        huddleId: 'huddle-valhalla-now',
        body: 'What is blocked?',
        authorId: 'human:jozef',
      }),
    );
    await waitFor(() => expect(screen.getAllByText('What is blocked?')).toHaveLength(1));
  });

  it('renders an error state when the service fails', async () => {
    const service = {
      ...createMockValkyrieService(),
      getDashboard: vi.fn().mockRejectedValue(new Error('unauthorized')),
    };
    render(<ValkyriePage />, {
      wrapper: wrapWithValkyrie({ valkyrie: service }),
    });

    expect(await screen.findByTestId('valkyrie-error-state')).toHaveTextContent('unauthorized');
  });

  it('renders an empty state when there are no environments', async () => {
    const service = {
      ...createMockValkyrieService(),
      getDashboard: vi.fn().mockResolvedValue({
        environments: [],
        valkyries: [],
        flocks: [],
        signals: [],
        operationalStates: [],
        judgments: [],
        courtDecisions: [],
        actions: [],
        huddles: [],
        learnings: [],
        updatedAt: '2026-06-03T14:10:00Z',
      }),
    };
    render(<ValkyriePage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: service,
        'valkyrie.signals': createMockValkyrieSignalStream([]),
      }),
    });

    await waitFor(() => expect(screen.getByText('No environments')).toBeInTheDocument());
  });
});
