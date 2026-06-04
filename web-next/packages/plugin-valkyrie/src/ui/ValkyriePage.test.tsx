import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ValkyriePage } from './ValkyriePage';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { createMockValkyrieService, createMockValkyrieSignalStream } from '../adapters/mock';

describe('ValkyriePage', () => {
  it('renders environment, flock, signal, state, huddle, and learning panels', async () => {
    render(<ValkyriePage />, { wrapper: wrapWithValkyrie() });

    expect(await screen.findByTestId('valkyrie-page')).toBeInTheDocument();
    expect(screen.getByTestId('environment-env-k8s-valhalla')).toBeInTheDocument();
    expect(screen.getByTestId('flock-flock-k8s')).toBeInTheDocument();
    expect(screen.getByTestId('signal-panel')).toBeInTheDocument();
    expect(screen.getByTestId('environment-state-panel')).toBeInTheDocument();
    expect(screen.getByTestId('huddle-panel')).toBeInTheDocument();
    expect(screen.getByTestId('learning-panel')).toBeInTheDocument();
    expect(screen.getByTestId('flock-live-report')).toHaveTextContent('K8s flock routing');
  });

  it('switches from environment learning to flock learning', async () => {
    const user = userEvent.setup();
    render(<ValkyriePage />, { wrapper: wrapWithValkyrie() });

    await user.click(await screen.findByTestId('flock-flock-k8s'));

    expect(
      screen.getByRole('heading', { level: 1, name: 'Kubernetes Valkyries' }),
    ).toBeInTheDocument();
    expect(screen.getByText('OOMKilled with rising queue depth')).toBeInTheDocument();
  });

  it('lets an operator join and speak in a huddle', async () => {
    const user = userEvent.setup();
    render(<ValkyriePage />, { wrapper: wrapWithValkyrie() });

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
