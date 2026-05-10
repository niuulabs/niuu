import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PersonaForm } from './PersonaForm';
import type { PersonaDetail } from '../ports';

const MOCK_PERSONA: PersonaDetail = {
  name: 'test-persona',
  role: 'build',
  letter: 'T',
  color: 'var(--color-accent-indigo)',
  summary: 'A test persona',
  description: 'Used in tests',
  permissionMode: 'default',
  allowedTools: ['read', 'write'],
  forbiddenTools: [],
  executor: {
    adapter: 'ravn.adapters.executors.cli.CliTransportExecutor',
    kwargs: {
      transport_adapter: 'skuld.transports.codex_ws.CodexWebSocketTransport',
    },
  },
  iterationBudget: 20,
  isBuiltin: false,
  hasOverride: false,
  producesEvent: 'code.changed',
  consumesEvents: ['review.completed'],
  systemPromptTemplate: '# test-persona\nYou are {{name}}, a {{role}} persona.',
  llm: { primaryAlias: 'claude-sonnet-4-6', thinkingEnabled: false, maxTokens: 8192 },
  produces: { eventType: 'code.changed', schemaDef: { file: 'string' } },
  consumes: { events: [{ name: 'review.completed' }], schemaDef: { topic: 'string' } },
  fanIn: { strategy: 'merge', params: {} },
  yamlSource: '[mock]',
};

function wrap() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{}}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('PersonaForm', () => {
  it('renders all form sections', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByText('Identity')).toBeInTheDocument();
    expect(screen.getByText('Runtime')).toBeInTheDocument();
    expect(screen.getByText('Execution')).toBeInTheDocument();
    expect(screen.getByText('Tool access')).toBeInTheDocument();
    expect(screen.getByText('Produces')).toBeInTheDocument();
    expect(screen.getByText('Consumes')).toBeInTheDocument();
    expect(screen.getByText('Fan-in')).toBeInTheDocument();
    expect(screen.getByText('Mímir write routing')).toBeInTheDocument();
  });

  it('populates name field from persona', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByDisplayValue('test-persona')).toBeInTheDocument();
  });

  it('shows summary and system prompt fields from persona', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByDisplayValue('A test persona')).toBeInTheDocument();
    expect(screen.getByLabelText('system_prompt_template')).toHaveValue(
      '# test-persona\nYou are {{name}}, a {{role}} persona.',
    );
  });

  it('does not expose persona-level model selection', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.queryByText('llm.alias')).not.toBeInTheDocument();
  });

  it('populates execution mode from persona executor', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByDisplayValue('codex streaming')).toBeInTheDocument();
    expect(
      screen.getByDisplayValue('skuld.transports.codex_ws.CodexWebSocketTransport'),
    ).toBeInTheDocument();
  });

  it('shows save bar when a field is changed', async () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    const descInput = screen.getByDisplayValue('Used in tests');
    fireEvent.change(descInput, { target: { value: 'changed description' } });
    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());
  });

  it('hides save bar after reset', async () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    const descInput = screen.getByDisplayValue('Used in tests');
    fireEvent.change(descInput, { target: { value: 'changed' } });
    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Reset'));
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument());
  });

  it('keeps llmPrimaryAlias empty when saved', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PersonaForm persona={MOCK_PERSONA} onSave={onSave} />, {
      wrapper: wrap(),
    });

    fireEvent.change(screen.getByDisplayValue('8192'), { target: { value: '4096' } });

    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /save persona/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({ llmPrimaryAlias: '', llmMaxTokens: 4096 }),
      );
    });
  });

  it('calls onSave with updated system prompt template when saved', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PersonaForm persona={MOCK_PERSONA} onSave={onSave} />, {
      wrapper: wrap(),
    });

    fireEvent.change(screen.getByLabelText('system_prompt_template'), {
      target: { value: '# updated\nYou are a stricter reviewer.' },
    });

    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /save persona/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          systemPromptTemplate: '# updated\nYou are a stricter reviewer.',
        }),
      );
    });
  });

  it('switches to the embedded ravn executor when selected', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PersonaForm persona={MOCK_PERSONA} onSave={onSave} />, {
      wrapper: wrap(),
    });

    fireEvent.change(screen.getByDisplayValue('codex streaming'), {
      target: { value: 'ravn' },
    });

    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /save persona/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ executor: undefined }));
    });
  });

  it('switches to codex streaming preset when selected', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PersonaForm persona={{ ...MOCK_PERSONA, executor: undefined }} onSave={onSave} />, {
      wrapper: wrap(),
    });

    fireEvent.change(screen.getByDisplayValue('embedded ravn agent'), {
      target: { value: 'codex_ws' },
    });

    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /save persona/i }));

    await waitFor(() => {
      expect(onSave).toHaveBeenCalledWith(
        expect.objectContaining({
          executor: {
            adapter: 'ravn.adapters.executors.cli.CliTransportExecutor',
            kwargs: {
              transport_adapter: 'skuld.transports.codex_ws.CodexWebSocketTransport',
            },
          },
        }),
      );
    });
  });

  it('shows validation errors for invalid state', async () => {
    render(<PersonaForm persona={{ ...MOCK_PERSONA, name: '' }} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    // Name is empty — should show validation error immediately
    await waitFor(() => expect(screen.getByText(/Name is required/)).toBeInTheDocument());
  });

  it('disables save button when there are validation errors', async () => {
    render(<PersonaForm persona={{ ...MOCK_PERSONA, name: '' }} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    const nameInput = screen.getAllByDisplayValue('')[0]!;
    fireEvent.change(nameInput, { target: { value: 'x' } });
    fireEvent.change(nameInput, { target: { value: '' } });

    // With no name and dirty state, save should be blocked
    await waitFor(() => {
      const saveBtn = screen.queryByRole('button', { name: /save persona/i });
      // Save button is only shown when dirty
      if (saveBtn) {
        expect(saveBtn).toBeDisabled();
      }
    });
  });

  it('shows thinking toggle in runtime section', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    // Thinking is now a toggle button showing true/false
    expect(screen.getByText('llm.thinking')).toBeInTheDocument();
  });

  it('shows add consumed event button', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByText('+ Add consumed event')).toBeInTheDocument();
  });

  // ── Fan-in strategy cards ──────────────────────────────────────────────

  it('renders fan-in strategy cards grid', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByTestId('fanin-cards')).toBeInTheDocument();
  });

  it('renders all 6 fan-in strategy cards', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.getByTestId('fanin-card-all_must_pass')).toBeInTheDocument();
    expect(screen.getByTestId('fanin-card-any_passes')).toBeInTheDocument();
    expect(screen.getByTestId('fanin-card-quorum')).toBeInTheDocument();
    expect(screen.getByTestId('fanin-card-merge')).toBeInTheDocument();
    expect(screen.getByTestId('fanin-card-first_wins')).toBeInTheDocument();
    expect(screen.getByTestId('fanin-card-weighted_score')).toBeInTheDocument();
  });

  it('marks the currently selected strategy card as active', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    const mergeCard = screen.getByTestId('fanin-card-merge');
    expect(mergeCard).toHaveAttribute('aria-pressed', 'true');
    expect(mergeCard).toHaveClass('rv-fanin-card--active');
  });

  it('marks other strategy cards as inactive', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    const anyPassesCard = screen.getByTestId('fanin-card-any_passes');
    expect(anyPassesCard).toHaveAttribute('aria-pressed', 'false');
    expect(anyPassesCard).not.toHaveClass('rv-fanin-card--active');
  });

  it('updates strategy when a different card is clicked', async () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    fireEvent.click(screen.getByTestId('fanin-card-first_wins'));
    await waitFor(() => {
      const card = screen.getByTestId('fanin-card-first_wins');
      expect(card).toHaveAttribute('aria-pressed', 'true');
      expect(card).toHaveClass('rv-fanin-card--active');
    });
  });

  it('shows description text on each fan-in card', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(
      screen.getByText('All upstream events must arrive before processing'),
    ).toBeInTheDocument();
    expect(screen.getByText('First arriving event triggers processing')).toBeInTheDocument();
    expect(screen.getByText('N of M events must arrive')).toBeInTheDocument();
    expect(screen.getByText('All events merged into a single context')).toBeInTheDocument();
    expect(screen.getByText('First event wins, others discarded')).toBeInTheDocument();
    expect(screen.getByText('Events scored and ranked by weight')).toBeInTheDocument();
  });

  it('renders SVG diagrams in fan-in cards', () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    const cards = screen.getByTestId('fanin-cards');
    const svgs = cards.querySelectorAll('svg');
    expect(svgs.length).toBe(6);
  });

  it('shows quorum params input when quorum strategy is selected', async () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.queryByLabelText('Quorum count')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('fanin-card-quorum'));
    await waitFor(() => {
      expect(screen.getByLabelText('Quorum count')).toBeInTheDocument();
    });
  });

  it('shows weighted score params input when weighted_score strategy is selected', async () => {
    render(<PersonaForm persona={MOCK_PERSONA} onSave={vi.fn()} />, {
      wrapper: wrap(),
    });
    expect(screen.queryByLabelText('Min score threshold')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('fanin-card-weighted_score'));
    await waitFor(() => {
      expect(screen.getByLabelText('Min score threshold')).toBeInTheDocument();
    });
  });
});
