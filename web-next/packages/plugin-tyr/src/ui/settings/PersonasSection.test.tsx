import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PersonasSection } from './PersonasSection';
import type { TyrPersonaSummary } from '../../ports';

const SEED_PERSONAS: TyrPersonaSummary[] = [
  {
    name: 'coder',
    permissionMode: 'workspace_write',
    allowedTools: ['Bash', 'Read', 'Write', 'Edit'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'code.changed',
    consumesEvents: [],
    model: 'sonnet-4.5',
    role: 'build',
  },
  {
    name: 'custom-agent',
    permissionMode: 'read_only',
    allowedTools: ['Read'],
    iterationBudget: 10,
    isBuiltin: false,
    hasOverride: true,
    producesEvent: '',
    consumesEvents: [],
    role: 'verify',
  },
];

function makeMockPersonaStore(yaml = 'name: coder\n') {
  return {
    listPersonas: async (_filter?: string) => SEED_PERSONAS,
    getPersonaYaml: async (_name: string) => yaml,
  };
}

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('PersonasSection', () => {
  it('shows loading state initially', () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    expect(screen.getByText(/loading personas/i)).toBeInTheDocument();
  });

  it('renders section heading', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('Persona overrides')).toBeInTheDocument());
  });

  it('shows persona names after loading', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    expect(screen.getByText('custom-agent')).toBeInTheDocument();
  });

  it('marks builtin persona with label', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('builtin')).toBeInTheDocument());
  });

  it('marks overridden persona with label', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('overridden')).toBeInTheDocument());
  });

  it('shows persona count', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('2 personas')).toBeInTheDocument());
  });

  it('shows YAML when persona is selected', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore('name: coder\nmodel: sonnet\n') }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    fireEvent.click(screen.getByText('coder'));
    await waitFor(() => expect(screen.getByText(/name: coder/)).toBeInTheDocument());
  });

  it('shows error state when service throws', async () => {
    const failing = {
      listPersonas: async () => {
        throw new Error('ravn unavailable');
      },
    };
    render(<PersonasSection />, { wrapper: wrap({ 'ravn.personas': failing }) });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('ravn unavailable')).toBeInTheDocument();
  });

  it('renders filter tabs', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() =>
      expect(screen.getByRole('tablist', { name: /persona filter/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('tab', { name: 'All' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Builtin' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Custom' })).toBeInTheDocument();
  });

  it('clicking a filter tab resets selection', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    fireEvent.click(screen.getByText('coder'));
    fireEvent.click(screen.getByRole('tab', { name: 'Custom' }));

    // After switching tab, the YAML panel should show the "select a persona" prompt
    await waitFor(() => {
      const matches = screen.getAllByText(/select a persona/i);
      // The YAML panel shows the prompt (should be at least one)
      expect(matches.length).toBeGreaterThanOrEqual(1);
    });
  });

  it('shows empty state when no personas match', async () => {
    const emptyStore = { listPersonas: async () => [] as TyrPersonaSummary[] };
    render(<PersonasSection />, { wrapper: wrap({ 'ravn.personas': emptyStore }) });
    await waitFor(() => expect(screen.getByText('No personas found.')).toBeInTheDocument());
  });

  it('shows budget chip for each persona', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    const budgetChips = screen.getAllByTestId('budget-chip');
    expect(budgetChips).toHaveLength(2);
    expect(budgetChips[0]).toHaveTextContent('budget 40');
    expect(budgetChips[1]).toHaveTextContent('budget 10');
  });

  it('shows model chip only for personas with a model', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    const modelChips = screen.getAllByTestId('model-chip');
    // Only "coder" has model set, custom-agent does not
    expect(modelChips).toHaveLength(1);
    expect(modelChips[0]).toHaveTextContent('model · sonnet-4.5');
  });

  it('shows Edit button for each persona', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    const editButtons = screen.getAllByTestId('edit-persona');
    expect(editButtons).toHaveLength(2);
  });

  it('renders PersonaAvatar for each persona', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    // PersonaAvatar renders with aria-label containing "persona"
    const avatars = screen.getAllByLabelText(/persona$/i);
    expect(avatars).toHaveLength(2);
  });

  it('uses role="option" for persona rows instead of button', async () => {
    render(<PersonasSection />, {
      wrapper: wrap({ 'ravn.personas': makeMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByText('coder')).toBeInTheDocument());
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(2);
  });
});
