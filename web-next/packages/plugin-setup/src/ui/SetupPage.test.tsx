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
    expect(initialStep(stateWith(['welcome', 'system']))).toBe('model');
    expect(
      initialStep(stateWith(['welcome', 'system', 'model', 'providers', 'git', 'tracker'])),
    ).toBe('runtime');
    expect(
      initialStep(
        stateWith([
          'welcome',
          'system',
          'model',
          'providers',
          'git',
          'tracker',
          'runtime',
          'launch',
        ]),
      ),
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

    await waitFor(() => expect(screen.getByTestId('setup-model')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('setup-model-skip')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-continue'));

    await waitFor(() => expect(screen.getByTestId('setup-step-providers')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('setup-provider-empty')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-provider-add'));
    fireEvent.click(screen.getByTestId('setup-add-pick-anthropic'));
    fireEvent.click(screen.getByTestId('setup-add-mode-key'));
    fireEvent.change(screen.getByTestId('setup-input-anthropic-api_key'), {
      target: { value: 'sk-ant' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-anthropic'));
    // the dialog closes by itself once the provider is connected
    await waitFor(() => expect(screen.queryByTestId('setup-add-dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId(/^setup-test-mock-/)).toBeInTheDocument());
    fireEvent.click(screen.getByTestId(/^setup-test-mock-/));
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

    await waitFor(() => expect(screen.getByTestId('setup-runtime')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('setup-access-lan')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-continue'));

    await waitFor(() => expect(screen.getByTestId('setup-finish')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('/ready'));
    expect((await service.getState()).completed).toBe(true);
    const steps = (await service.getState()).completedSteps;
    expect(steps.map((r) => r.step)).toContain('launch');
    expect(steps.find((r) => r.step === 'runtime')?.data).toEqual({
      bind_host: '0.0.0.0',
      external_host: '192.168.1.42',
    });
    expect(steps.find((r) => r.step === 'model')?.data).toEqual({
      vllm_enabled: false,
      vllm_model: '',
    });
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
      initialState: stateWith([
        'welcome',
        'system',
        'model',
        'providers',
        'git',
        'tracker',
        'runtime',
      ]),
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
      initialState: stateWith(['welcome', 'system', 'model']),
    });
    const service = {
      ...base,
      connectIntegration: async () => {
        throw new Error('rejected key');
      },
    };
    renderWithSetup(<SetupPage onNavigate={vi.fn()} />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-provider-add')).not.toBeDisabled());
    fireEvent.click(screen.getByTestId('setup-provider-add'));
    fireEvent.click(screen.getByTestId('setup-add-pick-openai'));
    fireEvent.click(screen.getByTestId('setup-add-mode-key'));
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
      initialState: stateWith([
        'welcome',
        'system',
        'model',
        'providers',
        'git',
        'tracker',
        'runtime',
      ]),
    });
    renderWithSetup(<SetupPage />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-finish')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() => expect(assign).toHaveBeenCalledWith('/ready?config=default'));
    vi.unstubAllGlobals();
  });
});

describe('SetupPage apply flow', () => {
  it('applies staged changes, waits for the restart, then finishes', async () => {
    const service = createMockSetupService({
      latencyMs: 0,
      applyPolls: 2,
      initialState: stateWith([
        'welcome',
        'system',
        'model',
        'providers',
        'git',
        'tracker',
        'runtime',
      ]),
    });
    await service.stageStack({ bind_host: '127.0.0.1' });
    const onNavigate = vi.fn();
    renderWithSetup(<SetupPage onNavigate={onNavigate} origin="http://192.168.1.42:8080" />, {
      service,
    });
    await waitFor(() => expect(screen.getByTestId('setup-finish-staged')).toBeInTheDocument());
    expect(screen.getByTestId('setup-finish-new-address')).toHaveTextContent(
      'http://127.0.0.1:8080',
    );
    expect(screen.getByText('Bringing Niuu up')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() => expect(screen.getByTestId('setup-finish-progress')).toBeInTheDocument());
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('/ready'), { timeout: 8000 });
    expect((await service.getState()).completed).toBe(true);
    expect((await service.getStack()).current.bindHost).toBe('127.0.0.1');
  }, 10000);

  it('reports a failed apply and lets the user try again', async () => {
    const base = createMockSetupService({
      latencyMs: 0,
      initialState: stateWith([
        'welcome',
        'system',
        'model',
        'providers',
        'git',
        'tracker',
        'runtime',
      ]),
    });
    await base.stageStack({ bind_host: '127.0.0.1' });
    const service = {
      ...base,
      applyStack: async () => {
        throw new Error('applier image missing');
      },
    };
    renderWithSetup(<SetupPage onNavigate={vi.fn()} />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-finish-staged')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('setup-finish-button'));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('applier image missing'),
    );
    expect(screen.getByTestId('setup-finish-button')).not.toBeDisabled();
  });

  it('shows the model step without a stack controller', async () => {
    const service = createMockSetupService({
      latencyMs: 0,
      stackAvailable: false,
      initialState: stateWith(['welcome', 'system']),
    });
    renderWithSetup(<SetupPage onNavigate={vi.fn()} />, { service });
    await waitFor(() => expect(screen.getByTestId('setup-model-unavailable')).toBeInTheDocument());
    expect(screen.getByTestId('setup-continue')).not.toBeDisabled();
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
    expect(screen.getByText(/Forge → New session/)).toBeInTheDocument();
    expect(screen.getByTestId('ready-dashboard')).toHaveAttribute('href', '/');
  });
});
