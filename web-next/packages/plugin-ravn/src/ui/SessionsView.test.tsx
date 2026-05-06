import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import { SessionsView } from './SessionsView';
import {
  createMockBudgetStream,
  createMockPersonaStore,
  createMockRavenStream,
  createMockSessionStream,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

const wrap = wrapWithServices;

function services() {
  return {
    'ravn.sessions': createMockSessionStream(),
    'ravn.ravens': createMockRavenStream(),
    'ravn.personas': createMockPersonaStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe('SessionsView', () => {
  it('shows loading state initially', () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it('renders the page-owned sessions rail after loading', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-page')).toBeInTheDocument());
    expect(screen.getByText(/10 active/i)).toBeInTheDocument();
    expect(screen.getByText(/2 closed/i)).toBeInTheDocument();
  });

  it('selects the newest running session by default and shows the header', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const header = await screen.findByTestId('sessions-header');
    expect(
      within(header).getByRole('heading', { name: 'Run integration tests' }),
    ).toBeInTheDocument();
    expect(within(header).getByText(/trigger:/i)).toBeInTheDocument();
  });

  it('clicking a rail item selects that session', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const target = await screen.findByRole('button', {
      name: 'Open session Review PR #142',
    });
    fireEvent.click(target);
    expect(target).toHaveAttribute('aria-pressed', 'true');
    const header = await screen.findByTestId('sessions-header');
    expect(within(header).getByRole('heading', { name: 'Review PR #142' })).toBeInTheDocument();
  });

  it('responds to ravn:session-selected events', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-page')).toBeInTheDocument());

    act(() => {
      window.dispatchEvent(
        new CustomEvent('ravn:session-selected', {
          detail: '10000001-0000-4000-8000-000000000005',
        }),
      );
    });

    const header = await screen.findByTestId('sessions-header');
    expect(within(header).getByRole('heading', { name: 'Review PR #142' })).toBeInTheDocument();
  });

  it('persists selection to localStorage', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const target = await screen.findByRole('button', {
      name: 'Open session Security audit — API endpoints',
    });
    fireEvent.click(target);
    expect(localStorage.getItem('ravn.session')).toBe('"10000001-0000-4000-8000-000000000004"');
  });

  it('renders transcript toolbar filters', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const group = await screen.findByRole('group', { name: /session transcript filter/i });
    expect(group).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'all' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'chat only' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ tools' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '+ system' })).toBeInTheDocument();
  });

  it('chat only filter hides the system init line', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const reviewSession = await screen.findByRole('button', {
      name: 'Open session Implement login form',
    });
    fireEvent.click(reviewSession);
    const log = await screen.findByRole('log', { name: /session transcript/i });
    expect(within(log).getByText(/session init/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'chat only' }));

    await waitFor(() => {
      expect(within(log).queryByText(/session init/i)).not.toBeInTheDocument();
      expect(within(log).getByText(/Please implement the login form/i)).toBeInTheDocument();
    });
  });

  it('shows context cards for summary, timeline, injects, emissions, and raven', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    await waitFor(() => expect(screen.getByTestId('sessions-context')).toBeInTheDocument());
    expect(screen.getByTestId('sessions-summary')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-timeline')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-injects')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-emissions')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-raven-card')).toBeInTheDocument();
  });

  it('shows a running composer for active sessions', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    expect(await screen.findByTestId('sessions-composer')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /session message composer/i })).toBeInTheDocument();
  });

  it('shows the read-only composer variant for closed sessions', async () => {
    render(<SessionsView />, { wrapper: wrap(services()) });
    const closedSession = await screen.findByRole('button', {
      name: 'Open session Monitor overnight alerts',
    });
    fireEvent.click(closedSession);
    expect(await screen.findByTestId('sessions-composer-closed')).toBeInTheDocument();
  });
});
