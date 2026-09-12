import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithSetup } from '../testing/renderWithSetup';
import { createMockSetupService } from '../adapters/mock';
import { SetupPage, initialStep } from './SetupPage';
import { ReadyPage } from './ReadyPage';

function stateWith(steps: string[]) {
  return {
    enabled: true,
    mode: 'docker',
    completed: false,
    completedAt: null,
    steps: [],
    completedSteps: steps.map((step) => ({ step, completedAt: 'now', data: {} })),
  };
}

describe('initialStep', () => {
  it('resumes at the first unfinished step', () => {
    expect(initialStep(undefined)).toBe('welcome');
    expect(initialStep(stateWith(['welcome']))).toBe('system');
    expect(initialStep(stateWith(['welcome', 'system', 'providers', 'git', 'tracker']))).toBe(
      'finish',
    );
    expect(
      initialStep(stateWith(['welcome', 'system', 'providers', 'git', 'tracker', 'launch'])),
    ).toBe('finish');
  });
});

describe('SetupPage', () => {
  it('walks from welcome to finish and completes', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const onNavigate = vi.fn();
    renderWithSetup(<SetupPage onNavigate={onNavigate} />, { service });

    await waitFor(() => expect(screen.getByTestId('setup-welcome')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('setup-host-chips')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-begin'));

    await waitFor(() => expect(screen.getByTestId('setup-system')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('setup-continue')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('setup-continue'));

    await waitFor(() => expect(screen.getByTestId('setup-step-providers')).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId('setup-integration-anthropic')).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByTestId('setup-input-anthropic-api_key'), {
      target: { value: 'sk-ant' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-anthropic'));
    await waitFor(() => expect(screen.getByTestId('setup-test-anthropic')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-test-anthropic'));
    await waitFor(() => expect(screen.getByTestId('setup-test-ok-anthropic')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-continue'));

    await waitFor(() => expect(screen.getByTestId('setup-step-git')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-back'));
    await waitFor(() => expect(screen.getByTestId('setup-step-providers')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-continue'));
    await waitFor(() => expect(screen.getByTestId('setup-step-git')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-continue'));
    await waitFor(() => expect(screen.getByTestId('setup-step-tracker')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-continue'));

    await waitFor(() => expect(screen.getByTestId('setup-finish')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('/ready'));
    expect((await service.getState()).completed).toBe(true);
    expect((await service.getState()).completedSteps.map((r) => r.step)).toContain('launch');
  });

  it('blocks the system step while a check fails', async () => {
    const service = createMockSetupService({
      latencyMs: 0,
      initialState: stateWith(['welcome']),
      system: {
        host: null,
        checks: [{ name: 'database', passed: false, warnOnly: false, message: 'down' }],
        healthy: false,
      },
    });
    renderWithSetup(<SetupPage onNavigate={vi.fn()} />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-system')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('setup-system-blocked')).toBeInTheDocument());
    expect(screen.getByTestId('setup-continue')).toBeDisabled();
    fireEvent.click(screen.getByTestId('setup-system-rerun'));
    await waitFor(() => expect(screen.getByTestId('setup-system-blocked')).toBeInTheDocument());
  });

  it('surfaces connect and finish errors', async () => {
    const base = createMockSetupService({
      latencyMs: 0,
      initialState: stateWith(['welcome', 'system', 'providers', 'git', 'tracker']),
    });
    const service = {
      ...base,
      complete: async () => {
        throw new Error('disk full');
      },
    };
    renderWithSetup(<SetupPage onNavigate={vi.fn()} />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-finish')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('disk full'));
  });

  it('shows a connect error on the card', async () => {
    const base = createMockSetupService({
      latencyMs: 0,
      initialState: stateWith(['welcome', 'system']),
    });
    const service = {
      ...base,
      connectIntegration: async () => {
        throw new Error('rejected key');
      },
    };
    renderWithSetup(<SetupPage onNavigate={vi.fn()} />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-integration-openai')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('setup-input-openai-api_key'), {
      target: { value: 'sk' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-openai'));
    await waitFor(() => expect(screen.getByText('rejected key')).toBeInTheDocument());
  });

  it('uses the browser location when no navigator is given', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', {
      ...window.location,
      assign,
      pathname: '/setup',
      search: '?config=default',
    });
    const service = createMockSetupService({
      latencyMs: 0,
      initialState: stateWith(['welcome', 'system', 'providers', 'git', 'tracker']),
    });
    renderWithSetup(<SetupPage />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-finish')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/ready?config=default'));
    vi.unstubAllGlobals();
  });
});

describe('ReadyPage', () => {
  it('shows the summary and destinations', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    await service.connectIntegration({
      slug: 'linear',
      credentialName: 'linear-setup',
      credential: { api_key: 'k' },
      config: {},
    });
    renderWithSetup(<ReadyPage />, { service });
    await waitFor(() => expect(screen.getByText('Tickets: linear')).toBeInTheDocument());
    expect(screen.getByText(/docker mode/)).toBeInTheDocument();
    expect(screen.getByText('Run your first session').closest('a')).toHaveAttribute(
      'href',
      '/volundr',
    );
    expect(screen.getByTestId('ready-dashboard')).toHaveAttribute('href', '/');
  });
});
