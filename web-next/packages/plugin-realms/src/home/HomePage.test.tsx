import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UI_MODE_STORAGE_KEY, readUiMode } from '@niuulabs/shell';
import type { UserFeaturePreference } from '@niuulabs/plugin-sdk';
import {
  createMockOdinReviewService,
  createSeedReviewItems,
  type ReviewItem,
} from '@niuulabs/plugin-valkyrie';
import type { ISessionStore, Session, SessionState } from '@niuulabs/plugin-volundr';
import { homePlugin } from './plugin';
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

function pendingReview(overrides: Partial<ReviewItem>): ReviewItem {
  const [seed] = createSeedReviewItems();
  return { ...seed!, ...overrides };
}

function fakeFeatures(update: (rows: UserFeaturePreference[]) => void) {
  return {
    getFeatureModules: async () => [],
    toggleFeature: async () => {
      throw new Error('not used');
    },
    getUserFeaturePreferences: async () => [],
    updateUserFeaturePreferences: async (rows: UserFeaturePreference[]) => {
      update(rows);
      return rows;
    },
  };
}

const withSessions = { sessionStore: fakeSessionStore() };

describe('homePlugin', () => {
  it('is the Simple-mode landing page and nothing in Advanced', () => {
    expect(homePlugin.id).toBe('home');
    expect(homePlugin.simple).toMatchObject({ only: true, landing: true });
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
        /realms, two sessions running, .* you\./,
      ),
    );
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

  it('answers an ask and drops the row before the server replies', async () => {
    const user = userEvent.setup();
    const reviews = createMockOdinReviewService([
      pendingReview({
        itemId: 'ask-1',
        title: 'Install the OOM probe',
        environmentId: 'env-k8s-valhalla',
      }),
      pendingReview({ itemId: 'ask-2', title: 'Raise the budget', environmentId: 'env-nowhere' }),
    ]);
    renderRealms('/home', { ...withSessions, 'valkyrie.reviews': reviews });
    await screen.findByTestId('home-needs-you-ask-1');
    expect(screen.getByTestId('home-needs-you-ask-1')).toHaveTextContent('Valhalla');
    expect(screen.getByTestId('home-needs-you-ask-2').querySelector('a')).toHaveAttribute(
      'href',
      '/valkyrie/inbox',
    );
    await user.click(screen.getByTestId('home-needs-you-ask-1').querySelector('button')!);
    expect(screen.queryByTestId('home-needs-you-ask-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('home-needs-you-ask-2')).toBeInTheDocument();
  });

  it('puts the row back when the decision fails', async () => {
    const user = userEvent.setup();
    const reviews = createMockOdinReviewService([pendingReview({ itemId: 'ask-1' })]);
    reviews.decideReview = vi.fn(async () => {
      throw new Error('reviews endpoint is down');
    });
    renderRealms('/home', { ...withSessions, 'valkyrie.reviews': reviews });
    await screen.findByTestId('home-needs-you-ask-1');
    await user.click(screen.getByTestId('home-needs-you-ask-1').querySelector('button')!);
    await screen.findByText(/reviews endpoint is down/);
    expect(screen.getByTestId('home-needs-you-ask-1')).toBeInTheDocument();
  });

  it('lists the sessions blocked on you and what to continue', async () => {
    renderRealms('/home', withSessions);
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
    renderRealms('/home', {
      sessionStore: fakeSessionStore([]),
      'valkyrie.reviews': createMockOdinReviewService([]),
    });
    await screen.findByText('Nothing is waiting on you.');
    expect(await screen.findByTestId('home-continue')).toBeInTheDocument();
  });

  it('switches to Advanced through the preferences service and opens the dashboard', async () => {
    const user = userEvent.setup();
    const rows: UserFeaturePreference[][] = [];
    const { router } = renderRealms('/home', {
      ...withSessions,
      features: fakeFeatures((written) => rows.push(written)),
    });
    await screen.findByTestId('home-page');
    await user.click(screen.getByTestId('home-open-advanced'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/volundr/forge'));
    expect(readUiMode()).toBe('advanced');
    expect(rows[0]!.find((row) => row.featureKey === 'ui.mode')?.visible).toBe(true);
    expect(rows[0]!.map((row) => row.featureKey)).toContain('home');
  });

  it('stays put and says why when the mode cannot be stored', async () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    const user = userEvent.setup();
    const features = fakeFeatures(() => {});
    features.updateUserFeaturePreferences = async () => {
      throw new Error('preferences endpoint is down');
    };
    const { router } = renderRealms('/home', { ...withSessions, features });
    await screen.findByTestId('home-page');
    await user.click(screen.getByTestId('home-open-advanced'));
    expect(await screen.findByRole('alert')).toHaveTextContent('preferences endpoint is down');
    expect(router.state.location.pathname).toBe('/home');
    expect(readUiMode()).toBe('simple');
  });
});
