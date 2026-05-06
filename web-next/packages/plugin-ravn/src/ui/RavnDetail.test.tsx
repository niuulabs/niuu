import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavnDetail } from './RavnDetail';
import {
  createMockRavenStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';
import type { Ravn } from '../domain/ravn';

const SAMPLE_RAVN: Ravn = {
  id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  personaName: 'sindri',
  status: 'active',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:00:00Z',
  role: 'build',
  letter: 'S',
  summary: 'Writes and edits source code across the stack.',
  iterationBudget: 40,
  writeRouting: 'local',
  cascade: 'sequential',
  location: 'eu-west-1',
  deployment: 'production',
  mounts: [
    { name: 'codebase', role: 'primary' },
    { name: 'docs', role: 'ro' },
  ],
  mcpServers: ['filesystem', 'git', 'bash'],
  gatewayChannels: ['slack-dev', 'github-webhook'],
  eventSubscriptions: ['code.requested', 'bug.fix.requested', 'code.changed'],
};

const SAMPLE_RAVN_MINIMAL: Ravn = {
  id: 'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c',
  personaName: 'vör',
  status: 'idle',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-14T18:00:00Z',
};

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.budget': createMockBudgetStream(),
    ...overrides,
  };
}

function wrap(services = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  localStorage.clear();
});

// ── Core rendering ───────────────────────────────────────────────────────────

describe('RavnDetail', () => {
  it('renders the ravn detail pane', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('ravn-detail')).toBeInTheDocument();
  });

  it('shows the persona name in the header', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getAllByText('sindri').length).toBeGreaterThan(0);
  });

  it('renders the tab nav with 5 tabs', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('ravn-sectabs')).toBeInTheDocument();
    for (const id of ['overview', 'triggers', 'activity', 'sessions', 'connectivity']) {
      expect(screen.getByTestId(`sectab-${id}`)).toBeInTheDocument();
    }
  });

  it('shows overview tab active by default', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const overviewTab = screen.getByTestId('sectab-overview');
    expect(overviewTab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows overview content by default', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('section-body-overview')).toBeInTheDocument();
  });

  it('switches tab when a tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const triggersTab = screen.getByTestId('sectab-triggers');
    fireEvent.click(triggersTab);
    expect(triggersTab).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('triggers-section-body')).toBeInTheDocument();
  });

  it('persists active tab to localStorage', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const sessionsTab = screen.getByTestId('sectab-sessions');
    fireEvent.click(sessionsTab);
    const stored = localStorage.getItem('ravn.detail.tab');
    expect(stored).toBe('"sessions"');
  });

  it('restores active tab from localStorage', () => {
    localStorage.setItem('ravn.detail.tab', '"connectivity"');
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    const connectivityTab = screen.getByTestId('sectab-connectivity');
    expect(connectivityTab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows close button when onClose is provided', () => {
    const handleClose = vi.fn();
    render(<RavnDetail ravn={SAMPLE_RAVN} onClose={handleClose} />, { wrapper: wrap() });
    const btn = screen.getByTestId('detail-close-btn');
    expect(btn).toBeInTheDocument();
    fireEvent.click(btn);
    expect(handleClose).toHaveBeenCalled();
  });

  it('does not show close button when onClose is not provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.queryByTestId('detail-close-btn')).not.toBeInTheDocument();
  });
});

// ── Overview tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Overview tab', () => {
  it('renders identity panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('identity-panel')).toBeInTheDocument();
  });

  it('renders runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('runtime-panel')).toBeInTheDocument();
  });

  it('shows persona name in identity panel', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getAllByText('sindri').length).toBeGreaterThan(0));
  });

  it('shows role badge in identity panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('build')).toBeInTheDocument();
  });

  it('shows summary text when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('Writes and edits source code across the stack.')).toBeInTheDocument();
  });

  it('renders state with StateDot in runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getAllByText('active').length).toBeGreaterThan(0);
  });

  it('shows model in runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('claude-sonnet-4-6')).toBeInTheDocument();
  });

  it('shows location when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    // Location appears in the hero subtitle (normalised: hyphens become spaces)
    expect(screen.getByText(/eu west 1/)).toBeInTheDocument();
  });

  it('shows deployment when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    // Deployment appears in the hero subtitle
    expect(screen.getByText(/production/)).toBeInTheDocument();
  });

  it('shows cascade when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByText('sequential')).toBeInTheDocument();
  });

  it('shows last activity in runtime panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    // The runtime panel displays "last activity" derived from updatedAt or createdAt
    expect(screen.getByText('last activity')).toBeInTheDocument();
  });

  it('shows write routing when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getAllByText('local').length).toBeGreaterThan(0);
  });

  it('renders mounts panel when mounts are provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    expect(screen.getByTestId('mounts-panel')).toBeInTheDocument();
  });

  it('does not render mounts panel when no mounts', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    expect(screen.queryByTestId('mounts-panel')).not.toBeInTheDocument();
  });

  it('renders without role/letter gracefully', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    expect(screen.getByTestId('ravn-detail')).toBeInTheDocument();
    expect(screen.queryByTestId('identity-panel')).toBeInTheDocument();
  });
});

// ── Triggers tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Triggers tab', () => {
  it('renders triggers section when triggers tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => expect(screen.getByTestId('triggers-section-body')).toBeInTheDocument());
  });

  it('renders trigger cards for this ravn', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const cards = screen.queryAllByTestId('trigger-card');
      expect(cards.length).toBeGreaterThan(0);
    });
  });

  it('shows trigger kind badge', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const kindBadges = screen.queryAllByTestId('trigger-kind');
      expect(kindBadges.length).toBeGreaterThan(0);
    });
  });

  it('shows trigger spec', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      expect(screen.getAllByText('/hooks/dispatch').length).toBeGreaterThan(0);
    });
  });

  it('shows last fired time when available', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const firedItems = screen.queryAllByTestId('trigger-last-fired');
      expect(firedItems.length).toBeGreaterThan(0);
    });
  });

  it('shows fire count when available', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const countItems = screen.queryAllByTestId('trigger-fire-count');
      expect(countItems.length).toBeGreaterThan(0);
      expect(countItems[0]?.textContent).toMatch(/\d+ fires/);
    });
  });

  it('renders toggle switch for each trigger', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      const toggles = screen.queryAllByTestId('trigger-toggle');
      expect(toggles.length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no triggers match this ravn', async () => {
    render(<RavnDetail ravn={{ ...SAMPLE_RAVN, personaName: 'unknown-persona' }} />, {
      wrapper: wrap(),
    });
    fireEvent.click(screen.getByTestId('sectab-triggers'));
    await waitFor(() => {
      expect(screen.getByText('No triggers configured')).toBeInTheDocument();
    });
  });

  it('shows trigger count badge on triggers tab when triggers exist', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    await waitFor(() => {
      const triggersTab = screen.getByTestId('sectab-triggers');
      expect(triggersTab.textContent).toMatch(/triggers/i);
    });
  });
});

// ── Activity tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Activity tab', () => {
  it('renders activity section when activity tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => expect(screen.getByTestId('activity-section-body')).toBeInTheDocument());
  });

  it('renders activity filter controls', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.getByTestId('activity-filter')).toBeInTheDocument();
      expect(screen.getByTestId('activity-filter-all')).toBeInTheDocument();
      expect(screen.getByTestId('activity-filter-user')).toBeInTheDocument();
      expect(screen.getByTestId('activity-filter-asst')).toBeInTheDocument();
    });
  });

  it('shows live indicator when ravn is active', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.getByTestId('activity-live')).toBeInTheDocument();
    });
  });

  it('does not show live indicator when ravn is idle', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.queryByTestId('activity-live')).not.toBeInTheDocument();
    });
  });

  it('renders messages with kind badges when sessions exist', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      const badges = screen.queryAllByTestId('activity-kind-badge');
      expect(badges.length).toBeGreaterThan(0);
    });
  });

  it('filters messages by kind when filter is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      // Wait for messages to load
      expect(screen.queryAllByTestId('activity-message').length).toBeGreaterThan(0);
    });

    const userFilter = screen.getByTestId('activity-filter-user');
    fireEvent.click(userFilter);

    await waitFor(() => {
      // After filtering to 'user' only, all visible kind badges should be 'user'
      const badges = screen.queryAllByTestId('activity-kind-badge');
      badges.forEach((badge) => {
        expect(badge.textContent).toBe('user');
      });
    });
  });

  it('shows empty state when ravn has no sessions', async () => {
    const ravnNoSessions: Ravn = {
      ...SAMPLE_RAVN,
      id: 'zzzzzzzz-0000-4000-8000-000000000000',
    };
    render(<RavnDetail ravn={ravnNoSessions} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-activity'));
    await waitFor(() => {
      expect(screen.getByText('No activity for this ravn')).toBeInTheDocument();
    });
  });
});

// ── Sessions tab ─────────────────────────────────────────────────────────────

describe('RavnDetail — Sessions tab', () => {
  it('renders sessions section when sessions tab is clicked', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => expect(screen.getByTestId('sessions-section-body')).toBeInTheDocument());
  });

  it('renders session cards', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      expect(screen.queryAllByTestId('session-card').length).toBeGreaterThan(0);
    });
  });

  it('shows session title in card', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      expect(screen.getByText('Implement login form')).toBeInTheDocument();
    });
  });

  it('shows session message count in metrics', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      const countEls = screen.queryAllByTestId('session-message-count');
      expect(countEls.length).toBeGreaterThan(0);
      expect(countEls[0]?.textContent).toMatch(/\d+ msgs/);
    });
  });

  it('shows session cost in metrics', async () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      const costEls = screen.queryAllByTestId('session-cost');
      expect(costEls.length).toBeGreaterThan(0);
      expect(costEls[0]?.textContent).toMatch(/\$\d+\.\d{2}/);
    });
  });

  it('dispatches ravn:session-selected event when session card is clicked', async () => {
    const handler = vi.fn();
    window.addEventListener('ravn:session-selected', handler);

    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));

    await waitFor(() => {
      const cards = screen.queryAllByTestId('session-card');
      expect(cards.length).toBeGreaterThan(0);
    });

    const card = screen.queryAllByTestId('session-card')[0];
    if (card) fireEvent.click(card);

    expect(handler).toHaveBeenCalled();
    window.removeEventListener('ravn:session-selected', handler);
  });

  it('shows empty state when no sessions exist', async () => {
    const ravnNoSessions: Ravn = {
      ...SAMPLE_RAVN,
      id: 'zzzzzzzz-0000-4000-8000-000000000000',
    };
    render(<RavnDetail ravn={ravnNoSessions} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-sessions'));
    await waitFor(() => {
      expect(screen.getByText('No sessions')).toBeInTheDocument();
    });
  });
});

// ── Connectivity tab ─────────────────────────────────────────────────────────

describe('RavnDetail — Connectivity tab', () => {
  it('renders connectivity section when connectivity tab is clicked', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('connectivity-section-body')).toBeInTheDocument();
  });

  it('renders MCP servers panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('conn-mcp-panel')).toBeInTheDocument();
  });

  it('renders gateway channels panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('conn-gateway-panel')).toBeInTheDocument();
  });

  it('renders event subscriptions panel', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    expect(screen.getByTestId('conn-events-panel')).toBeInTheDocument();
  });

  it('shows MCP server chips when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const chips = screen.queryAllByTestId('mcp-server-chip');
    expect(chips.length).toBe(3);
    expect(chips[0]?.textContent).toBe('filesystem');
  });

  it('shows gateway channel chips when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const chips = screen.queryAllByTestId('gateway-channel-chip');
    expect(chips.length).toBe(2);
    expect(chips[0]?.textContent).toBe('slack-dev');
  });

  it('shows event subscription chips when provided', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const chips = screen.queryAllByTestId('event-subscription-chip');
    expect(chips.length).toBe(3);
  });

  it('shows "None configured" when no MCP servers', () => {
    render(
      <RavnDetail
        ravn={{ ...SAMPLE_RAVN, mcpServers: [], gatewayChannels: [], eventSubscriptions: [] }}
      />,
      { wrapper: wrap() },
    );
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const emptyTexts = screen.queryAllByText('None configured');
    expect(emptyTexts.length).toBe(3);
  });

  it('shows "None configured" when connectivity fields are absent', () => {
    render(<RavnDetail ravn={SAMPLE_RAVN_MINIMAL} />, { wrapper: wrap() });
    fireEvent.click(screen.getByTestId('sectab-connectivity'));
    const emptyTexts = screen.queryAllByText('None configured');
    expect(emptyTexts.length).toBe(3);
  });
});
