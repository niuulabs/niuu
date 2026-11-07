import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import type { ISessionStore, Session, SessionState } from '@niuulabs/plugin-volundr';
import { homePlugin } from './plugin';
import { createCallLog, fakeVolundr, workspaceWithWaitingTicket } from '../testing/fakes';
import { renderRealms } from '../testing/renderRealms';

function session(id: string, state: SessionState, lastActivityAt: string): Session {
  return {
    id,
    ravnId: 'r',
    name: `session ${id}`,
    personaName: 'p',
    templateId: 't',
    clusterId: 'c',
    state,
    startedAt: '2026-09-01T00:00:00Z',
    lastActivityAt,
    resources: {
      cpuRequest: 0,
      cpuLimit: 0,
      cpuUsed: 0,
      memRequestMi: 0,
      memLimitMi: 0,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
  };
}

const SESSIONS = [
  session('blocked-1', 'awaiting_input', '2026-09-05T12:00:00Z'),
  session('live-1', 'running', '2026-09-05T10:00:00Z'),
  session('live-2', 'idle', '2026-09-05T09:00:00Z'),
  session('done-1', 'terminated', '2026-09-06T09:00:00Z'),
];

function fakeSessionStore(sessions: Session[] = SESSIONS): ISessionStore {
  return {
    getSession: async (id) => sessions.find((entry) => entry.id === id) ?? null,
    listSessions: async () => sessions,
    createSession: async () => {
      throw new Error('not used');
    },
    updateSession: async () => {
      throw new Error('not used');
    },
    deleteSession: async () => {},
    subscribe: () => () => {},
  };
}

const withSessions = { sessionStore: fakeSessionStore() };

describe('homePlugin', () => {
  it('is a plain rail plugin with no tabs', () => {
    expect(homePlugin.id).toBe('home');
    expect(homePlugin.system).toBeUndefined();
    expect(homePlugin.tabs).toBeUndefined();
    expect(homePlugin.title).toBe('Home');
  });
});

describe('HomePage', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('greets by name, says where things stand, and shows the status chips', async () => {
    renderRealms('/home', withSessions);
    await screen.findByTestId('home-page');
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Good morning, Dev.'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('home-chips')).toHaveTextContent('1 repositories'),
    );
    expect(screen.getByTestId('home-chips')).toHaveTextContent('1 boards');
    expect(screen.getByTestId('home-chips')).toHaveTextContent(/models · \d+ providers/);
    await waitFor(() =>
      expect(screen.getByTestId('home-page')).toHaveTextContent(
        /workspaces, two sessions running, .* you\./,
      ),
    );
  });

  it('points nowhere at Realms while the Realms plugin is off', async () => {
    renderRealms('/home', withSessions, undefined, [homePlugin]);
    await screen.findByTestId('home-choice-session');

    expect(screen.queryByTestId('home-choice-realm')).toBeNull();
    await screen.findByTestId('home-needs-you');
    expect(screen.queryByRole('link', { name: 'see all' })).toBeNull();
    expect(document.querySelector('a[href^="/realms"]')).toBeNull();
  });

  it('offers exactly three ways to start, each linking to the page that does it', async () => {
    renderRealms('/home', withSessions);
    await screen.findByTestId('home-choice-session');
    expect(screen.getByTestId('home-choice-session').querySelector('a')).toHaveAttribute(
      'href',
      '/volundr/sessions/new',
    );
    expect(screen.getByTestId('home-choice-workflow').querySelector('a')).toHaveAttribute(
      'href',
      '/ting/workflows',
    );
    expect(screen.getByTestId('home-choice-realm').querySelector('a')).toHaveAttribute(
      'href',
      '/realms/new',
    );
  });

  it('leaves the board chip out when no tracker is wired', async () => {
    renderRealms('/home', { ...withSessions, 'ting.tracker': undefined });
    await screen.findByTestId('home-page');
    await waitFor(() =>
      expect(screen.getByTestId('home-chips')).toHaveTextContent('1 repositories'),
    );
    expect(screen.getByTestId('home-chips')).not.toHaveTextContent('boards');
  });

  it('puts a ticket at a gate among what needs you', async () => {
    const log = createCallLog();
    const services = await workspaceWithWaitingTicket(log, 'valhalla');
    renderRealms('/home', { ...withSessions, ...services }, log);
    expect(await screen.findByTestId('needs-you-ticket-run-1')).toHaveTextContent('Valhalla');
    expect(screen.getByRole('link', { name: 'Review' })).toHaveAttribute(
      'href',
      // Review links carry the page they were opened from, so the review can lead back.
      '/ting/review/link-1/issue-1?returnTo=%2Fhome',
    );
  });

  it('lists the sessions blocked on you and what to continue', async () => {
    const log = createCallLog();
    const services = await workspaceWithWaitingTicket(log, 'valhalla');
    const volundr = {
      ...fakeVolundr(log),
      getSessions: async () => [
        {
          id: 'ws-1',
          status: 'running',
          coordination: { labels: ['work-project:link-1'] },
        },
      ],
    };
    renderRealms('/home', { ...withSessions, ...services, volundr }, log);
    await screen.findByTestId('home-needs-you-session-blocked-1');
    const cont = await screen.findByTestId('home-continue');
    expect(cont).toHaveTextContent('session live-1');
    expect(cont).toHaveTextContent('session live-2');
    expect(cont).toHaveTextContent('session done-1');
    expect(cont).not.toHaveTextContent('session blocked-1');
    await waitFor(() =>
      expect(screen.getByTestId('home-continue-realm-valhalla')).toBeInTheDocument(),
    );
  });

  it('says so plainly when nothing is waiting or running', async () => {
    renderRealms('/home', { sessionStore: fakeSessionStore([]) });
    await screen.findByText('Nothing is waiting on you.');
    expect(await screen.findByTestId('home-continue')).toBeInTheDocument();
  });
});
