import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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
    expect(screen.getByTestId('valkyrie-event-log')).toHaveTextContent('Signal tail');
    expect(screen.getByTestId('valkyrie-event-log')).toHaveTextContent(
      'Prepare a guarded rollout fix',
    );
    expect(screen.getByTestId('valkyrie-court-panel')).toHaveTextContent('ODIN court');
    expect(screen.getByTestId('valkyrie-actions-panel')).toHaveTextContent(
      'prepare_rollout_remediation',
    );
    expect(screen.getByTestId('valkyrie-work-queue')).toHaveTextContent('Pod checkout/api');
    expect(screen.getByTestId('valkyrie-llm-status')).toHaveTextContent(
      'Qwen/Qwen3.6-35B-A3B-FP8',
    );
    expect(screen.getByTestId('valkyrie-live-runtime')).toHaveTextContent(
      'Sigrun',
    );
    expect(screen.getByTestId('valkyrie-live-runtime')).toHaveTextContent(
      'Evidence-first cluster guardian',
    );
    expect(screen.queryByTestId('valkyrie-telemetry-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('signal-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('huddle-panel')).not.toBeInTheDocument();
    expect(screen.queryByText('Printer forge')).not.toBeInTheDocument();
    expect(
      screen.queryByText('External sender asks for review before Friday'),
    ).not.toBeInTheDocument();
  });

  it('renders verified route views from live telemetry', async () => {
    const { rerender } = render(<ValkyriePage defaultView="topology" />, {
      wrapper: wrapWithValkyrie(),
    });

    expect(await screen.findByTestId('valkyrie-topology-view')).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-live-environments')).toHaveTextContent(
      'env-k8s-valhalla',
    );

    rerender(<ValkyriePage defaultView="lineage" />);
    expect(await screen.findByTestId('valkyrie-lineage-view')).toHaveTextContent(
      'Prepare a guarded rollout fix',
    );

    rerender(<ValkyriePage defaultView="learning" />);
    expect(await screen.findByTestId('valkyrie-learning-ops')).toHaveTextContent(
      'learning.dream.completed',
    );
    expect(screen.getByTestId('valkyrie-tool-needs')).toHaveTextContent(
      'prepare_rollout_remediation',
    );

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

  it('lets an operator join and speak in a huddle', async () => {
    const user = userEvent.setup();
    render(<ValkyriePage />, {
      wrapper: wrapWithValkyrie({
        valkyrie: createMockValkyrieService(createDemoDashboard()),
      }),
    });

    await user.click(await screen.findByRole('button', { name: 'Join' }));
    await user.type(screen.getByLabelText(/message valhalla memory/i), 'What is blocked?');
    await user.click(screen.getByRole('button', { name: 'Send' }));

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
