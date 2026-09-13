import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { createMockSetupService, MOCK_MODELS } from '../adapters/mock';
import { memoryPlan } from '../domain/setup';
import { ModelStep, choiceFor, parseModelIds } from './ModelStep';

describe('memoryPlan', () => {
  it('splits accelerator memory into model, sandboxes and free', () => {
    expect(memoryPlan(128, 62)).toEqual({
      totalGib: 128,
      modelGib: 62,
      sandboxGib: 12,
      freeGib: 54,
      modelPct: 48,
      sandboxPct: 9,
    });
    expect(memoryPlan(128, 0).sandboxGib).toBe(0);
    expect(memoryPlan(0, 62).modelPct).toBe(0);
    expect(memoryPlan(16, 62).freeGib).toBe(0);
  });
});

describe('ModelStep', () => {
  const base = { loading: false, unavailable: null, staging: false, stageError: null };

  it('lists curated models with fit verdicts and stages a pick', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const stack = await service.getStack();
    const onStage = vi.fn();
    render(<ModelStep {...base} stack={stack} onStage={onStage} />);
    expect(screen.getByText(/Recommended for 128 GB/)).toBeInTheDocument();
    expect(screen.getByTestId('setup-model-nemotron-3-nano-30b')).toHaveTextContent('Fits');
    expect(screen.getByTestId('setup-model-skip')).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('setup-model-nemotron-3-nano-30b'));
    expect(onStage).toHaveBeenCalledWith({
      vllm_enabled: true,
      vllm_model: MOCK_MODELS[0]!.model,
    });
    fireEvent.click(screen.getByTestId('setup-model-skip'));
    expect(onStage).toHaveBeenCalledWith({ vllm_enabled: false });
  });

  it('reflects a staged curated model in the memory meter', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const stack = await service.stageStack({
      vllm_enabled: true,
      vllm_model: MOCK_MODELS[0]!.model,
    });
    render(<ModelStep {...base} stack={stack} onStage={vi.fn()} />);
    expect(screen.getByTestId('setup-model-nemotron-3-nano-30b')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('setup-model-memory')).toHaveTextContent('62 GB');
    expect(screen.getByTestId('setup-model-memory')).toHaveTextContent('54 GB');
    expect(choiceFor(stack)).toEqual({ kind: 'curated', id: 'nemotron-3-nano-30b' });
  });

  it('takes a custom model id and validates it', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const stack = await service.getStack();
    const onStage = vi.fn();
    render(<ModelStep {...base} stack={stack} onStage={onStage} />);
    fireEvent.click(screen.getByTestId('setup-model-custom-use'));
    expect(onStage).not.toHaveBeenCalled();
    expect(screen.getByText('Enter a Hugging Face model id')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('setup-model-custom-input'), {
      target: { value: ' org/custom ' },
    });
    fireEvent.click(screen.getByTestId('setup-model-custom-use'));
    expect(onStage).toHaveBeenCalledWith({ vllm_enabled: true, vllm_model: 'org/custom' });
    const custom = await service.stageStack({ vllm_enabled: true, vllm_model: 'org/custom' });
    expect(choiceFor(custom)).toEqual({ kind: 'custom' });
    expect(choiceFor(undefined)).toEqual({ kind: 'skip' });
  });

  it('disables models that do not fit and explains an unknown host', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const stack = await service.getStack();
    const tight = {
      ...stack,
      acceleratorMemoryGib: 32,
      models: stack.models.map((m) => ({ ...m, fits: m.weightGib < 30 })),
    };
    render(<ModelStep {...base} stack={tight} onStage={vi.fn()} />);
    expect(screen.getByTestId('setup-model-gpt-oss-120b')).toBeDisabled();
    expect(screen.getByTestId('setup-model-gpt-oss-120b')).toHaveTextContent('Does not fit');
    expect(screen.getByTestId('setup-model-qwen3-coder-30b')).not.toBeDisabled();

    const unknown = {
      ...stack,
      acceleratorMemoryGib: 0,
      models: stack.models.map((m) => ({ ...m, fits: null })),
    };
    render(<ModelStep {...base} stack={unknown} onStage={vi.fn()} />);
    expect(screen.getByText('No accelerator memory reported')).toBeInTheDocument();
    expect(screen.getAllByText(/Needs ~/).length).toBeGreaterThan(0);
  });

  it('shows the current model, errors and the unavailable state', async () => {
    const service = createMockSetupService({
      latencyMs: 0,
      initialStack: {
        vllm: {
          enabled: true,
          model: 'org/live',
          image: 'i',
          maxModelLen: 1,
          gpuMemoryUtilization: 0.5,
        },
      },
    });
    const stack = await service.getStack();
    render(
      <ModelStep {...base} stack={stack} stageError={new Error('bad model')} onStage={vi.fn()} />,
    );
    expect(screen.getByTestId('setup-model-current')).toHaveTextContent('org/live');
    expect(screen.getByRole('alert')).toHaveTextContent('bad model');

    render(
      <ModelStep
        {...base}
        stack={undefined}
        unavailable={new Error('Stack changes are not available')}
        onStage={vi.fn()}
      />,
    );
    expect(screen.getByTestId('setup-model-unavailable')).toHaveTextContent('not available');
  });
});

describe('ModelStep with a model server you already run', () => {
  const base = { loading: false, unavailable: null, staging: false, stageError: null };

  it('validates the server form and stages it, stopping vLLM', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const stack = await service.getStack();
    const onStage = vi.fn();
    render(<ModelStep {...base} stack={stack} onStage={onStage} />);
    fireEvent.click(screen.getByTestId('setup-model-server-use'));
    expect(onStage).not.toHaveBeenCalled();
    expect(screen.getByText('Enter an http(s) URL')).toBeInTheDocument();
    expect(screen.getByText('Enter at least one model id')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('setup-model-server-url'), {
      target: { value: 'http://host.docker.internal:11434' },
    });
    fireEvent.change(screen.getByTestId('setup-model-server-models'), {
      target: { value: 'llama3.2:latest, qwen3:8b' },
    });
    fireEvent.change(screen.getByTestId('setup-model-server-key'), { target: { value: ' k ' } });
    fireEvent.click(screen.getByTestId('setup-model-server-use'));
    expect(onStage).toHaveBeenCalledWith({
      vllm_enabled: false,
      model_server_enabled: true,
      model_server_url: 'http://host.docker.internal:11434',
      model_server_models: ['llama3.2:latest', 'qwen3:8b'],
      model_server_api_key: 'k',
    });
  });

  it('shows a staged server as the choice and stops it when vLLM is picked instead', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const stack = await service.stageStack({
      vllm_enabled: false,
      model_server_enabled: true,
      model_server_url: 'http://host.docker.internal:11434',
      model_server_models: ['llama3.2:latest'],
    });
    expect(choiceFor(stack)).toEqual({ kind: 'server' });
    const onStage = vi.fn();
    render(<ModelStep {...base} stack={stack} onStage={onStage} />);
    expect(screen.getByTestId('setup-model-server')).toHaveClass('setup-option--selected');
    expect(screen.getByTestId('setup-model-server-url')).toHaveValue(
      'http://host.docker.internal:11434',
    );
    fireEvent.click(screen.getByTestId('setup-model-nemotron-3-nano-30b'));
    expect(onStage).toHaveBeenCalledWith({
      model_server_enabled: false,
      vllm_enabled: true,
      vllm_model: MOCK_MODELS[0]!.model,
    });
  });

  it('parses model ids from commas and newlines', () => {
    expect(parseModelIds(' a/b ,c:latest\n\n d,')).toEqual(['a/b', 'c:latest', 'd']);
  });
});
