import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { createMockSetupService } from '../adapters/mock';
import type { ApplyStatus } from '../domain/setup';
import { renderWithSetup } from '../testing/renderWithSetup';
import { LocalModelCard, currentProgress, formatGb } from './LocalModelCard';

const base: ApplyStatus = {
  state: 'applied',
  startedAt: 'now',
  detail: 'Applied.',
  changes: {},
  vllm: null,
  progress: null,
};

describe('LocalModelCard', () => {
  it('shows the image pull with a bar while the apply runs', () => {
    const status: ApplyStatus = {
      ...base,
      state: 'applying',
      progress: {
        phase: 'pulling',
        detail: 'Pulling the vllm image · 2.1 of 7.3 GB · 4 of 12 layers done',
        completedBytes: 2.1 * 1000 ** 3,
        totalBytes: 7.3 * 1000 ** 3,
      },
      vllm: { state: 'absent', detail: 'The vLLM container has not been created yet.' },
    };
    renderWithSetup(<LocalModelCard status={status} />);
    expect(screen.getByTestId('setup-local-model-title')).toHaveTextContent(
      'Pulling the vLLM image',
    );
    expect(screen.getByTestId('setup-local-model-detail')).toHaveTextContent('2.1 of 7.3 GB');
    expect(screen.getByTestId('setup-local-model-bar')).toHaveAttribute('aria-valuenow', '29');
    expect(screen.getByText('29%')).toBeInTheDocument();
    expect(screen.getByText('2.1 GB of 7.3 GB')).toBeInTheDocument();
  });

  it('shows the model download after the apply, sweeping when the size is unknown', () => {
    const status: ApplyStatus = {
      ...base,
      vllm: {
        state: 'starting',
        detail: 'Downloading org/m · 3.0 GB so far',
        progress: {
          phase: 'downloading',
          detail: 'Downloading org/m · 3.0 GB so far',
          completedBytes: 3 * 1000 ** 3,
          totalBytes: 0,
        },
      },
    };
    renderWithSetup(<LocalModelCard status={status} />);
    expect(screen.getByTestId('setup-local-model-title')).toHaveTextContent(
      'Downloading the model',
    );
    expect(screen.getByText('Working…')).toBeInTheDocument();
    expect(screen.getByTestId('setup-local-model-bar')).toHaveClass('setup-meter--busy');
    expect(screen.getByTestId('setup-local-model-bar')).not.toHaveAttribute('aria-valuenow');
  });

  it('names the restart and the failure', () => {
    const restarting = renderWithSetup(
      <LocalModelCard
        status={{ ...base, state: 'applying', vllm: { state: 'absent', detail: '' } }}
        reconnecting
      />,
    );
    expect(screen.getByTestId('setup-local-model-title')).toHaveTextContent(
      'Restarting the platform',
    );
    restarting.unmount();
    renderWithSetup(
      <LocalModelCard
        status={{ ...base, vllm: { state: 'failed', detail: 'exec: --: invalid option' } }}
      />,
    );
    expect(screen.getByTestId('setup-local-model-title')).toHaveTextContent('Local model failed');
    expect(screen.getByTestId('setup-local-model-detail')).toHaveTextContent('invalid option');
    expect(screen.queryByTestId('setup-local-model-bar')).not.toBeInTheDocument();
  });

  it('tests the model once it serves and lets the user test again', async () => {
    const service = createMockSetupService({ latencyMs: 0, modelPolls: 0 });
    await service.stageStack({ vllm_enabled: true, vllm_model: 'org/m' });
    // walk the mock to "serving": apply, then poll past the model start-up
    await service.applyStack();
    for (let i = 0; i < 4; i += 1) await service.stackStatus();
    const testModel = vi.spyOn(service, 'testModel');
    const status: ApplyStatus = {
      ...base,
      vllm: { state: 'ready', detail: 'Serving org/m.' },
    };
    renderWithSetup(<LocalModelCard status={status} />, { service });
    expect(screen.getByTestId('setup-local-model-title')).toHaveTextContent(
      'Local model is serving',
    );
    await waitFor(() =>
      expect(screen.getByTestId('setup-local-model-result')).toHaveTextContent('Answered "OK"'),
    );
    expect(testModel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('setup-local-model-test-btn'));
    await waitFor(() => expect(testModel).toHaveBeenCalledTimes(2));
  });

  it('reports a model that does not answer', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'testModel').mockResolvedValue({
      ok: false,
      model: 'org/m',
      reply: '',
      latencyMs: 90000,
      detail: 'HTTP 503: loading',
    });
    renderWithSetup(
      <LocalModelCard status={{ ...base, vllm: { state: 'ready', detail: 'Serving org/m.' } }} />,
      { service },
    );
    await waitFor(() =>
      expect(screen.getByTestId('setup-local-model-result')).toHaveTextContent(
        'No answer: HTTP 503: loading',
      ),
    );
    expect(screen.getByTestId('setup-local-model-test-btn')).toHaveTextContent('Test again');
  });

  it('shows the request error when the test call fails', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'testModel').mockRejectedValue(new Error('not serving yet'));
    renderWithSetup(
      <LocalModelCard status={{ ...base, vllm: { state: 'ready', detail: 'Serving org/m.' } }} />,
      { service },
    );
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('not serving yet'));
  });

  it('renders nothing without a status', () => {
    const { container } = renderWithSetup(<LocalModelCard status={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('helpers', () => {
  it('formats sizes and picks the current progress', () => {
    expect(formatGb(0)).toBe('0 GB');
    expect(formatGb(2.14 * 1000 ** 3)).toBe('2.1 GB');
    expect(formatGb(62 * 1000 ** 3)).toBe('62 GB');
    expect(currentProgress(undefined)).toBeNull();
    const pulling = { phase: 'pulling', detail: 'p', completedBytes: 1, totalBytes: 2 };
    const loading = { phase: 'loading', detail: 'l', completedBytes: 1, totalBytes: 4 };
    expect(
      currentProgress({
        ...base,
        state: 'applying',
        progress: pulling,
        vllm: { state: 'starting', detail: '', progress: loading },
      }),
    ).toBe(pulling);
    expect(
      currentProgress({ ...base, vllm: { state: 'starting', detail: '', progress: loading } }),
    ).toBe(loading);
    expect(currentProgress({ ...base, vllm: null })).toBeNull();
  });
});
