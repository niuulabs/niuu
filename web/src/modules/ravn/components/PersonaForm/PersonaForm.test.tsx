import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PersonaForm } from './PersonaForm';
import type { PersonaDetail } from '../../api/types';

function mkDetail(overrides: Partial<PersonaDetail> = {}): PersonaDetail {
  return {
    name: 'test-persona',
    permissionMode: 'read-only',
    allowedTools: ['file', 'git'],
    forbiddenTools: [],
    iterationBudget: 10,
    isBuiltin: false,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
    systemPromptTemplate: 'You are a test agent.',
    llm: { primaryAlias: 'balanced', thinkingEnabled: false, maxTokens: 0 },
    produces: { eventType: '', schemaDef: {} },
    consumes: { eventTypes: [], injects: [] },
    fanIn: { strategy: 'merge', contributesTo: '' },
    yamlSource: '[built-in]',
    ...overrides,
  };
}

describe('PersonaForm', () => {
  it('renders name field', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
  });

  it('renders system prompt field', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/template/i)).toBeInTheDocument();
  });

  it('renders submit button with default label', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
  });

  it('renders custom submit label', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} submitLabel="Create" />);
    expect(screen.getByRole('button', { name: /create/i })).toBeInTheDocument();
  });

  it('renders cancel button', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('calls onCancel when cancel button clicked', () => {
    const onCancel = vi.fn();
    render(<PersonaForm onSubmit={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('shows validation error when name is empty', async () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => {
      expect(screen.getByText(/name is required/i)).toBeInTheDocument();
    });
  });

  it('shows validation error when system prompt is empty', async () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: 'my-agent' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => {
      expect(screen.getByText(/system prompt is required/i)).toBeInTheDocument();
    });
  });

  it('shows validation error for invalid name characters', async () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: 'invalid name!' } });
    fireEvent.change(screen.getByLabelText(/template/i), { target: { value: 'prompt' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => {
      expect(screen.getByText(/letters, numbers, hyphens/i)).toBeInTheDocument();
    });
  });

  it('calls onSubmit with correct data when form is valid', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<PersonaForm onSubmit={onSubmit} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: 'my-agent' } });
    fireEvent.change(screen.getByLabelText(/template/i), { target: { value: 'You are helpful.' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledOnce();
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'my-agent',
          systemPromptTemplate: 'You are helpful.',
        })
      );
    });
  });

  it('pre-fills form with initial persona data', () => {
    render(<PersonaForm initial={mkDetail()} onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/^name/i)).toHaveValue('test-persona');
    expect(screen.getByLabelText(/template/i)).toHaveValue('You are a test agent.');
  });

  it('name field is disabled when initial persona provided', () => {
    render(<PersonaForm initial={mkDetail()} onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/^name/i)).toBeDisabled();
  });

  it('renders all section headings', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Identity')).toBeInTheDocument();
    expect(screen.getByText('System Prompt')).toBeInTheDocument();
    expect(screen.getByText('Tools & Permissions')).toBeInTheDocument();
    expect(screen.getByText('LLM Settings')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Contract')).toBeInTheDocument();
  });

  it('renders tool preview when allowed tools typed', async () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/allowed tools/i), { target: { value: 'file, git' } });
    await waitFor(() => {
      expect(screen.getByText('file')).toBeInTheDocument();
      expect(screen.getByText('git')).toBeInTheDocument();
    });
  });

  it('renders iteration budget field', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/iteration budget/i)).toBeInTheDocument();
  });

  it('renders extended thinking checkbox', () => {
    render(<PersonaForm onSubmit={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByLabelText(/extended thinking/i)).toBeInTheDocument();
  });
});
