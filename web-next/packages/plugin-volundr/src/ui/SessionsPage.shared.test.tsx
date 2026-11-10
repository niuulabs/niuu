import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { SessionsPage } from './SessionsPage';
import { createMockVolundrService } from '../adapters/mock';
import { createMockNotificationFeed } from '../adapters/notifications.mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { IVolundrService } from '../ports/IVolundrService';
import type { Session } from '../domain/session';

const navigate = vi.fn();
let params: { sessionId?: string } = {};

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useParams: () => params,
}));

vi.mock('./LiveSessionDetailPage', () => ({
  LiveSessionDetailPage: ({ sessionId }: { sessionId: string }) => (
    <div data-testid="live-session-detail-page">{sessionId}</div>
  ),
}));

vi.mock('./LaunchWizard', () => ({
  LaunchWizard: ({ open }: { open: boolean }) =>
    open ? <div data-testid="launch-wizard">Launch</div> : null,
}));

vi.stubGlobal(
  'IntersectionObserver',
  class {
    observe = vi.fn();
    disconnect = vi.fn();
    unobserve = vi.fn();
  },
);

function makeSession(
  overrides: Partial<Session> & Pick<Session, 'id' | 'personaName' | 'state'>,
): Session {
  return {
    ravnId: `ravn-${overrides.id}`,
    templateId: 'tpl-default',
    clusterId: 'valhalla',
    clusterName: 'Valhalla',
    startedAt: '2026-05-01T00:00:00.000Z',
    lastActivityAt: '2026-05-01T00:05:00.000Z',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.5,
      memRequestMi: 512,
      memLimitMi: 1024,
      memUsedMi: 256,
      gpuCount: 0,
    },
    env: {},
    events: [],
    ...overrides,
  };
}

const SESSIONS = [
  makeSession({
    id: 'mine',
    name: 'my-work',
    personaName: 'Mine',
    state: 'idle',
    ownerId: 'bob',
    coordination: { projectId: 'lexi', role: 'worker' },
  }),
  makeSession({
    id: 'shared-approver',
    name: 'alice-feature',
    personaName: 'Alice feature',
    state: 'awaiting_input',
    ownerId: 'alice',
    room: { role: 'approver', participantStatus: 'active', expiresAt: null },
    coordination: { projectId: 'lexi', role: 'worker' },
  }),
  makeSession({
    id: 'shared-viewer',
    name: 'stranger-work',
    personaName: 'Stranger work',
    state: 'idle',
    ownerId: 'someone-outside',
    room: { role: 'viewer', participantStatus: 'active', expiresAt: null },
    coordination: { projectId: 'lexi', role: 'worker' },
  }),
  makeSession({
    id: 'invited',
    name: 'one-off',
    personaName: 'One off',
    state: 'idle',
    ownerId: 'alice',
    room: { role: 'viewer', participantStatus: 'active', expiresAt: null },
  }),
];

function store(sessions: Session[]): ISessionStore {
  return {
    getSession: async (id) => sessions.find((session) => session.id === id) ?? null,
    listSessions: async () => sessions,
    createSession: async () => {
      throw new Error('not implemented');
    },
    updateSession: async () => {
      throw new Error('not implemented');
    },
    deleteSession: async () => {},
    subscribe: () => () => {},
  };
}

function setup(sessions: Session[] = SESSIONS) {
  const volundr: IVolundrService = createMockVolundrService();
  volundr.getProjects = vi
    .fn()
    .mockResolvedValue([{ id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active', revision: 4 }]);
  volundr.getProjectMembers = vi.fn().mockResolvedValue([
    {
      project_id: 'lexi',
      user_id: 'alice',
      role: 'owner',
      expertise: '',
      skills: [],
      display_name: 'Alice Smith',
      email: 'alice@example.test',
    },
  ]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr) },
          sessionStore: store(sessions),
          'volundr.notifications': createMockNotificationFeed({ seed: [] }),
        }}
      >
        <SessionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return volundr;
}

describe('SessionsPage shared sessions', () => {
  beforeEach(() => {
    localStorage.clear();
    navigate.mockClear();
    params = {};
  });

  it("lists a shared session under its project beside the member's own, with role and owner", async () => {
    const volundr = setup();
    const group = await screen.findByTestId('pod-group-lexi');

    expect(await within(group).findByTestId('pod-entry-mine')).toBeInTheDocument();
    expect(within(group).getByTestId('pod-entry-shared-approver')).toBeInTheDocument();
    const badge = await within(group).findByText('Shared · approver · Alice Smith');
    expect(badge).toHaveAttribute('title', 'Shared by Alice Smith');
    expect(within(group).queryByTestId('session-shared-mine')).not.toBeInTheDocument();
    expect(volundr.getProjectMembers).toHaveBeenCalledWith(expect.objectContaining({ id: 'lexi' }));
  });

  it('says only "Shared" and the role when no project names the owner', async () => {
    setup();
    const group = await screen.findByTestId('pod-group-lexi');

    const badge = await within(group).findByTestId('session-shared-shared-viewer');
    expect(badge).toHaveTextContent(/^Shared · viewer$/);
    expect(badge).toHaveAttribute('title', 'Shared with you');
  });

  it('files a shared session without a project outside the folders', async () => {
    setup();
    const entry = await screen.findByTestId('pod-entry-invited');

    expect(entry.closest('[data-testid="pod-group-lexi"]')).toBeNull();
    expect(within(entry).getByTestId('session-shared-invited')).toBeInTheDocument();
  });

  it("offers the owner's actions only on the member's own sessions", async () => {
    setup();
    await screen.findByTestId('pod-entry-mine');

    expect(screen.getByRole('button', { name: 'Actions for my-work' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Actions for alice-feature' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Actions for stranger-work' })).toBeNull();
  });
});
