import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    id: 'lead',
    name: 'coordinator-niu-1117',
    personaName: 'Coordinator',
    state: 'idle',
    coordination: { projectId: 'lexi', role: 'coordinator' },
  }),
  makeSession({
    id: 'worker',
    name: 'implement-ticket',
    personaName: 'Worker',
    state: 'awaiting_input',
    coordination: {
      projectId: 'lexi',
      role: 'worker',
      parent: { instanceId: 'valhalla', sessionId: 'lead' },
    },
  }),
  makeSession({
    id: 'solo',
    name: 'independent-work',
    personaName: 'Solo',
    state: 'idle',
    readState: {
      isUnread: true,
      latestOutputSeq: 3,
      readThroughSeq: 1,
      manuallyUnread: false,
      revision: 1,
    },
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
  volundr.getProjects = vi.fn().mockResolvedValue([
    { id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active', revision: 4 },
    { id: 'empty', name: 'Empty one', slug: 'empty', status: 'active', revision: 1 },
  ]);
  volundr.updateProject = vi.fn(async (project, changes) => ({
    ...project,
    ...changes,
    revision: (project.revision ?? 1) + 1,
  }));
  const sessionStore = store(sessions);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = () => (
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr) },
          sessionStore,
          'volundr.notifications': createMockNotificationFeed({ seed: [] }),
        }}
      >
        <SessionsPage />
      </ServicesProvider>
    </QueryClientProvider>
  );
  return { volundr, ...render(tree()), tree };
}

async function openLexi() {
  const group = await screen.findByTestId('pod-group-lexi');
  fireEvent.click(within(group).getByTitle('Open Lexi'));
  return screen.findByTestId('project-home');
}

describe('SessionsPage project home', () => {
  beforeEach(() => {
    localStorage.clear();
    navigate.mockClear();
    params = {};
  });

  it('opens the project’s home from its folder, fills the folder and leaves no session open', async () => {
    setup();
    const group = await screen.findByTestId('pod-group-lexi');
    // Before: the default session shows in the detail pane.
    expect(await screen.findByTestId('live-session-detail-page')).toBeInTheDocument();

    const home = await openLexi();
    expect(within(home).getByRole('heading', { level: 1, name: 'Lexi' })).toBeInTheDocument();
    expect(navigate).toHaveBeenLastCalledWith({ to: '/volundr/sessions' });
    expect(screen.queryByTestId('live-session-detail-page')).not.toBeInTheDocument();
    // The home speaks for the project's sessions only.
    expect(within(home).getByTestId('project-session-lead')).toBeInTheDocument();
    expect(within(home).queryByTestId('project-session-solo')).not.toBeInTheDocument();

    const folder = within(group).getByTitle('Open Lexi');
    expect(folder).toHaveAttribute('aria-current', 'true');
    expect(folder.closest('.forge-project-folder')).toHaveAttribute('data-selected', 'true');
    // No session row is the open one, and the folder's counts keep their test ids.
    for (const row of screen.getAllByTestId(/^pod-entry-/)) {
      expect(row).not.toHaveAttribute('aria-current');
    }
    expect(screen.getByTestId('pod-group-lexi-count')).toHaveTextContent('2');
    expect(screen.getByTestId('pod-group-lexi-needs-you')).toHaveTextContent('1');
    expect(screen.getByTestId('sessions-page')).toHaveAttribute('data-detail-open', 'true');
  });

  it('opens a project that has no sessions yet', async () => {
    setup();
    const group = await screen.findByTestId('pod-group-empty-one');
    fireEvent.click(within(group).getByTitle('Open Empty one'));
    const home = await screen.findByTestId('project-home');
    expect(within(home).getByText('No sessions yet')).toBeInTheDocument();
  });

  it('folds and unfolds with the caret without opening the project', async () => {
    setup();
    const group = await screen.findByTestId('pod-group-lexi');
    expect(await within(group).findByTestId('pod-entry-lead')).toBeInTheDocument();
    const caret = within(group).getByRole('button', { name: 'Collapse Lexi' });
    expect(caret).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(caret);
    expect(within(group).queryByTestId('pod-entry-lead')).not.toBeInTheDocument();
    const expand = within(group).getByRole('button', { name: 'Expand Lexi' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    expect(localStorage.getItem('niuu.forge.group.lexi')).toBe('1');
    expect(screen.queryByTestId('project-home')).not.toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.click(expand);
    expect(within(group).getByTestId('pod-entry-lead')).toBeInTheDocument();
    expect(screen.queryByTestId('project-home')).not.toBeInTheDocument();
  });

  it('opens a session from the home and closes the home', async () => {
    setup();
    const home = await openLexi();
    fireEvent.click(within(home).getByTestId('project-attention-worker'));
    expect(navigate).toHaveBeenLastCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'worker' },
    });
    expect(screen.queryByTestId('project-home')).not.toBeInTheDocument();
    expect(await screen.findByTestId('live-session-detail-page')).toHaveTextContent('worker');
  });

  it('closes the home when a session is chosen from the list', async () => {
    setup();
    await openLexi();
    fireEvent.click(screen.getByTestId('pod-entry-solo'));
    expect(screen.queryByTestId('project-home')).not.toBeInTheDocument();
    expect(await screen.findByTestId('live-session-detail-page')).toHaveTextContent('solo');
  });

  it('closes the home when the route opens a session', async () => {
    const { rerender, tree } = setup();
    await openLexi();
    params = { sessionId: 'solo' };
    rerender(tree());
    await waitFor(() => expect(screen.queryByTestId('project-home')).not.toBeInTheDocument());
    expect(await screen.findByTestId('live-session-detail-page')).toHaveTextContent('solo');
  });

  it('closes the home from the mobile back button', async () => {
    setup();
    await openLexi();
    navigate.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '‹ Sessions' }));
    expect(screen.queryByTestId('project-home')).not.toBeInTheDocument();
    expect(navigate).toHaveBeenCalledWith({ to: '/volundr/sessions' });
    expect(screen.getByTestId('sessions-page')).toHaveAttribute('data-detail-open', 'false');
  });

  it('closes the home when the list is no longer grouped by project', async () => {
    setup();
    await openLexi();
    fireEvent.click(screen.getByTestId('session-list-menu'));
    fireEvent.click(screen.getByTestId('pod-group-mode-state'));
    await waitFor(() => expect(screen.queryByTestId('project-home')).not.toBeInTheDocument());
  });

  it('gives way to the unread stream when that filter is chosen', async () => {
    setup();
    await openLexi();
    fireEvent.click(screen.getByTestId('session-filter-unread'));
    await waitFor(() => expect(screen.queryByTestId('project-home')).not.toBeInTheDocument());
    expect(screen.getByTestId('unread-list')).toBeInTheDocument();
  });

  it('opens the launch wizard from New session', async () => {
    setup();
    const home = await openLexi();
    expect(screen.queryByTestId('launch-wizard')).not.toBeInTheDocument();
    fireEvent.click(within(home).getByRole('button', { name: 'New session' }));
    expect(screen.getByTestId('launch-wizard')).toBeInTheDocument();
  });

  it('saves settings on the project record and leaves the home when it is archived', async () => {
    const { volundr } = setup();
    const home = await openLexi();
    fireEvent.click(within(home).getByRole('button', { name: 'Settings…' }));
    await screen.findByRole('dialog', { name: 'Project settings' });
    fireEvent.change(screen.getByLabelText('Brief'), { target: { value: 'Ship it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(volundr.updateProject).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'lexi', revision: 4 }),
        { brief: 'Ship it' },
      ),
    );
    // The projects list was refreshed after the save.
    await waitFor(() => expect(volundr.getProjects).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole('button', { name: 'Archive project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Archive it' }));
    await waitFor(() => expect(screen.queryByTestId('project-home')).not.toBeInTheDocument());
  });

  it('names a project the viewer cannot read as shared, never by its id', async () => {
    setup([
      makeSession({
        id: 'x',
        name: 'stray',
        personaName: 'Stray',
        state: 'idle',
        coordination: { projectId: 'ghost', role: 'worker' },
      }),
    ]);
    const group = await screen.findByTestId('pod-group-shared-with-you');
    fireEvent.click(within(group).getByTitle('Open Shared with you'));
    const home = await screen.findByTestId('project-home');
    expect(
      within(home).getByRole('heading', { level: 1, name: 'Shared with you' }),
    ).toBeInTheDocument();
    expect(within(home).getByRole('button', { name: 'Settings…' })).toBeDisabled();
  });
});
