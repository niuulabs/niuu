import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { PersonasView } from './PersonasView';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

function mkSummary(name: string, isBuiltin = true) {
  return {
    name,
    permission_mode: 'workspace-write',
    allowed_tools: ['file', 'git'],
    iteration_budget: 40,
    is_builtin: isBuiltin,
    has_override: false,
    produces_event: '',
    consumes_events: [],
  };
}

function mockOk(data: unknown) {
  mockFetch.mockResolvedValue({
    status: 200,
    ok: true,
    json: async () => data,
  });
}

function wrap(element: React.ReactElement) {
  return render(<MemoryRouter>{element}</MemoryRouter>);
}

describe('PersonasView', () => {
  it('shows loading state initially', () => {
    mockOk([]);
    wrap(<PersonasView />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('falls back to mock data when no personas returned', async () => {
    mockOk([]);
    wrap(<PersonasView />);
    await waitFor(() => {
      expect(screen.getByText(/demo data/i)).toBeInTheDocument();
    });
  });

  it('renders persona cards from API', async () => {
    mockOk([mkSummary('coding-agent'), mkSummary('research-agent')]);
    wrap(<PersonasView />);
    await waitFor(() => {
      expect(screen.getByText('coding-agent')).toBeInTheDocument();
      expect(screen.getByText('research-agent')).toBeInTheDocument();
    });
  });

  it('falls back to mock data on API failure', async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'error' }),
    });
    wrap(<PersonasView />);
    await waitFor(() => {
      expect(screen.getByText(/demo data/i)).toBeInTheDocument();
    });
  });

  it('renders filter buttons', () => {
    mockOk([]);
    wrap(<PersonasView />);
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Built-in' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Custom' })).toBeInTheDocument();
  });

  it('renders New Persona button', () => {
    mockOk([]);
    wrap(<PersonasView />);
    expect(screen.getByRole('button', { name: /new persona/i })).toBeInTheDocument();
  });

  it('changes filter to Built-in on click', async () => {
    mockOk([mkSummary('coding-agent')]);
    wrap(<PersonasView />);

    await waitFor(() => screen.getByText('coding-agent'));

    mockOk([mkSummary('coding-agent')]);
    fireEvent.click(screen.getByRole('button', { name: 'Built-in' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('source=builtin'),
        expect.any(Object)
      );
    });
  });

  it('changes filter to Custom on click', async () => {
    mockOk([]);
    wrap(<PersonasView />);

    await waitFor(() => screen.getByText(/demo data/i));

    mockOk([mkSummary('my-custom', false)]);
    fireEvent.click(screen.getByRole('button', { name: 'Custom' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('source=custom'),
        expect.any(Object)
      );
    });
  });

  it('renders built-in badge on built-in persona card', async () => {
    mockOk([mkSummary('coding-agent', true)]);
    wrap(<PersonasView />);
    await waitFor(() => {
      expect(screen.getByText('built-in')).toBeInTheDocument();
    });
  });

  it('renders multiple personas as a grid of cards', async () => {
    mockOk([mkSummary('coding-agent'), mkSummary('research-agent'), mkSummary('planning-agent')]);
    wrap(<PersonasView />);
    await waitFor(() => {
      expect(screen.getAllByRole('link')).toHaveLength(3);
    });
  });
});
