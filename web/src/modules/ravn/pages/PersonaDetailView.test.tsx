import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PersonaDetailView } from './PersonaDetailView';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const mockDetail = {
  name: 'coding-agent',
  permission_mode: 'workspace-write',
  allowed_tools: ['file', 'git', 'terminal'],
  forbidden_tools: ['cascade'],
  iteration_budget: 40,
  is_builtin: true,
  has_override: false,
  produces_event: 'code.completed',
  consumes_events: ['task.assigned'],
  system_prompt_template: 'You are a coding agent.',
  llm: { primary_alias: 'balanced', thinking_enabled: true, max_tokens: 0 },
  produces: { event_type: 'code.completed', schema_def: {} },
  consumes: { event_types: ['task.assigned'], injects: ['repo', 'branch'] },
  fan_in: { strategy: 'merge', contributes_to: '' },
  yaml_source: '[built-in]',
};

function mockDetailAndYaml() {
  mockFetch
    .mockResolvedValueOnce({ status: 200, ok: true, json: async () => mockDetail })
    .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'name: coding-agent\n' });
}

function wrap(name: string) {
  return render(
    <MemoryRouter initialEntries={[`/ravn/personas/${name}`]}>
      <Routes>
        <Route path="/ravn/personas/:name" element={<PersonaDetailView />} />
        <Route path="/ravn/personas" element={<div>Personas List</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('PersonaDetailView', () => {
  it('shows loading state initially', () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('renders persona name after load', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('coding-agent')).toBeInTheDocument();
    });
  });

  it('renders built-in badge', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('built-in')).toBeInTheDocument();
    });
  });

  it('renders Identity section', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('Identity')).toBeInTheDocument();
    });
  });

  it('renders permission mode', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('workspace-write')).toBeInTheDocument();
    });
  });

  it('renders iteration budget', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('40')).toBeInTheDocument();
    });
  });

  it('renders Tools & Permissions section', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('Tools & Permissions')).toBeInTheDocument();
    });
  });

  it('renders allowed tools', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('file')).toBeInTheDocument();
      expect(screen.getByText('git')).toBeInTheDocument();
      expect(screen.getByText('terminal')).toBeInTheDocument();
    });
  });

  it('renders forbidden tools', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('cascade')).toBeInTheDocument();
    });
  });

  it('renders LLM Settings section', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('LLM Settings')).toBeInTheDocument();
    });
  });

  it('renders llm alias', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('balanced')).toBeInTheDocument();
    });
  });

  it('renders thinking enabled', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('enabled')).toBeInTheDocument();
    });
  });

  it('renders Pipeline Contract section', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('Pipeline Contract')).toBeInTheDocument();
    });
  });

  it('renders produces event', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('code.completed')).toBeInTheDocument();
    });
  });

  it('renders System Prompt section', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('System Prompt')).toBeInTheDocument();
    });
  });

  it('renders system prompt text', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('You are a coding agent.')).toBeInTheDocument();
    });
  });

  it('renders action buttons', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /fork/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument();
    });
  });

  it('does not render delete button for built-in personas', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument();
    });
  });

  it('renders delete button for custom personas', async () => {
    mockFetch
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ ...mockDetail, is_builtin: false }),
      })
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'yaml: content\n' });

    wrap('my-custom');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /delete/i })).toBeInTheDocument();
    });
  });

  it('shows YAML panel when Raw YAML toggle clicked', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));
    fireEvent.click(screen.getByText(/raw yaml/i));
    await waitFor(() => {
      expect(screen.getByText(/name: coding-agent/)).toBeInTheDocument();
    });
  });

  it('shows fork panel when Fork clicked', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));
    fireEvent.click(screen.getByRole('button', { name: /fork/i }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText('new-persona-name')).toBeInTheDocument();
    });
  });

  it('shows edit form when Edit clicked', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => {
      expect(screen.getByText(/edit:/i)).toBeInTheDocument();
    });
  });

  it('shows error state on fetch failure', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'not found' }),
    });
    wrap('nonexistent');
    await waitFor(() => {
      expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    });
  });

  it('shows create form for ~new sentinel', () => {
    wrap('~new');
    expect(screen.getByText('New Persona')).toBeInTheDocument();
  });

  it('renders back button', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /← back/i })).toBeInTheDocument();
    });
  });

  it('renders override badge when hasOverride is true', async () => {
    mockFetch
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ ...mockDetail, has_override: true }),
      })
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'yaml: content\n' });
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('override')).toBeInTheDocument();
    });
  });

  it('shows unlimited when iterationBudget is 0', async () => {
    mockFetch
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ ...mockDetail, iteration_budget: 0 }),
      })
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'yaml: content\n' });
    wrap('coding-agent');
    await waitFor(() => {
      expect(screen.getByText('unlimited')).toBeInTheDocument();
    });
  });

  it('calls create API when create form submitted and navigates', async () => {
    mockFetch.mockResolvedValue({
      status: 201,
      ok: true,
      json: async () => ({ ...mockDetail, name: 'new-agent' }),
    });
    wrap('~new');

    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: 'new-agent' } });
    fireEvent.change(screen.getByLabelText(/template/i), {
      target: { value: 'You are helpful.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/personas'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  it('shows action error when create fails', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'Name already taken' }),
    });
    wrap('~new');

    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: 'new-agent' } });
    fireEvent.change(screen.getByLabelText(/template/i), {
      target: { value: 'You are helpful.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => {
      expect(screen.getByText('Name already taken')).toBeInTheDocument();
    });
  });

  it('saves updates and returns to view mode on successful edit', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));

    mockFetch.mockResolvedValueOnce({
      status: 200,
      ok: true,
      json: async () => ({ ...mockDetail }),
    });

    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => screen.getByText(/edit:/i));

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText('Identity')).toBeInTheDocument();
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/coding-agent'),
        expect.objectContaining({ method: 'PUT' })
      );
    });
  });

  it('shows action error when update fails', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'Server update error' }),
    });

    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    await waitFor(() => screen.getByText(/edit:/i));

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText('Server update error')).toBeInTheDocument();
    });
  });

  it('navigates to personas list after successful delete', async () => {
    mockFetch
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ ...mockDetail, is_builtin: false }),
      })
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'yaml: content\n' })
      .mockResolvedValueOnce({ status: 204, ok: true, json: async () => ({}) });

    vi.spyOn(window, 'confirm').mockReturnValue(true);
    wrap('coding-agent');
    await waitFor(() => screen.getByRole('button', { name: /delete/i }));

    fireEvent.click(screen.getByRole('button', { name: /delete/i }));

    await waitFor(() => {
      expect(screen.getByText('Personas List')).toBeInTheDocument();
    });
  });

  it('does not navigate when delete API fails', async () => {
    mockFetch
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ ...mockDetail, is_builtin: false }),
      })
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'yaml: content\n' })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'Delete failed on server' }),
      });

    vi.spyOn(window, 'confirm').mockReturnValue(true);
    wrap('coding-agent');
    await waitFor(() => screen.getByRole('button', { name: /delete/i }));

    fireEvent.click(screen.getByRole('button', { name: /delete/i }));

    await waitFor(() => {
      expect(screen.getByText('Delete failed on server')).toBeInTheDocument();
    });
    expect(screen.queryByText('Personas List')).not.toBeInTheDocument();
  });

  it('does not navigate when delete confirm is declined', async () => {
    mockFetch
      .mockResolvedValueOnce({
        status: 200,
        ok: true,
        json: async () => ({ ...mockDetail, is_builtin: false }),
      })
      .mockResolvedValueOnce({ status: 200, ok: true, json: async () => 'yaml: content\n' });

    vi.spyOn(window, 'confirm').mockReturnValue(false);
    wrap('coding-agent');
    await waitFor(() => screen.getByRole('button', { name: /delete/i }));

    fireEvent.click(screen.getByRole('button', { name: /delete/i }));

    expect(screen.queryByText('Personas List')).not.toBeInTheDocument();
  });

  it('calls fork API when fork form submitted', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));

    mockFetch.mockResolvedValue({
      status: 201,
      ok: true,
      json: async () => ({ ...mockDetail, name: 'forked-agent', is_builtin: false }),
    });

    fireEvent.click(screen.getByRole('button', { name: 'Fork' }));
    await waitFor(() => screen.getByPlaceholderText('new-persona-name'));

    fireEvent.change(screen.getByPlaceholderText('new-persona-name'), {
      target: { value: 'forked-agent' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create fork/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('fork'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  it('shows action error when fork fails', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));

    mockFetch.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Fork name already exists' }),
    });

    fireEvent.click(screen.getByRole('button', { name: 'Fork' }));
    await waitFor(() => screen.getByPlaceholderText('new-persona-name'));

    fireEvent.change(screen.getByPlaceholderText('new-persona-name'), {
      target: { value: 'taken-name' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create fork/i }));

    await waitFor(() => {
      expect(screen.getByText('Fork name already exists')).toBeInTheDocument();
    });
  });

  it('cancel fork returns to view mode', async () => {
    mockDetailAndYaml();
    wrap('coding-agent');
    await waitFor(() => screen.getByText('coding-agent'));

    fireEvent.click(screen.getByRole('button', { name: 'Fork' }));
    await waitFor(() => screen.getByPlaceholderText('new-persona-name'));

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByPlaceholderText('new-persona-name')).not.toBeInTheDocument();
    });
  });
});
