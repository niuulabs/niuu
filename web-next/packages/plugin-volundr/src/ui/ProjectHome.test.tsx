import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { createMockVolundrService } from '../adapters/mock';
import { createMockNotificationFeed } from '../adapters/notifications.mock';
import type { SessionNotification } from '../domain/notifications';
import type { Session } from '../domain/session';
import type { ForgeProject } from '../models/volundr.model';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { ProjectHome, resolvedAsks, type ProjectHomeProps } from './ProjectHome';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

const NOW = Date.parse('2026-11-05T12:00:00Z');
const hoursAgo = (hours: number) => new Date(NOW - hours * 3_600_000).toISOString();

function makeSession(overrides: Partial<Session> & Pick<Session, 'id' | 'state'>): Session {
  return {
    name: overrides.id,
    ravnId: 'ravn',
    personaName: 'Worker',
    templateId: 'tpl',
    clusterId: 'valhalla',
    clusterName: 'Valhalla',
    startedAt: hoursAgo(10),
    lastActivityAt: hoursAgo(1),
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 1,
      memLimitMi: 2,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    model: 'gpt-5.6-terra',
    coordination: { projectId: 'p-1', role: 'worker' },
    ...overrides,
  };
}

function makeNotification(
  overrides: Partial<SessionNotification> & Pick<SessionNotification, 'id' | 'seq'>,
): SessionNotification {
  return {
    instanceId: 'valhalla',
    instanceName: 'Valhalla',
    sessionId: 'waiting',
    sessionSeq: overrides.seq,
    sessionName: 'waiting',
    ownerId: 'u',
    projectId: 'p-1',
    kind: 'info',
    severity: 'info',
    source: 'agent',
    title: 'Notice',
    body: '',
    links: [],
    engine: null,
    model: null,
    correlationId: null,
    reviewId: null,
    speak: null,
    createdAt: hoursAgo(overrides.seq),
    read: false,
    ...overrides,
  };
}

const PROJECT: ForgeProject = {
  id: 'p-1',
  name: 'A2A Interoperability',
  slug: 'a2a',
  status: 'active',
  revision: 2,
};

const SESSIONS: Session[] = [
  makeSession({
    id: 'waiting',
    state: 'awaiting_input',
    shortDescription: 'Delivery blocked: approve or send it back',
    activityStateSince: hoursAgo(6),
  }),
  makeSession({
    id: 'lead',
    state: 'failed',
    lastActivityAt: hoursAgo(6),
    coordination: { projectId: 'p-1', role: 'coordinator' },
  }),
  makeSession({ id: 'old-run', state: 'terminated', terminatedAt: hoursAgo(30) }),
  makeSession({ id: 'archived-one', state: 'archived' }),
  makeSession({
    id: 'elsewhere',
    state: 'running',
    coordination: { projectId: 'other', role: 'worker' },
  }),
];

const WORKER_COUNTS = new Map([['lead', 5]]);

function renderHome(
  props: Partial<ProjectHomeProps> = {},
  options: {
    notifications?: SessionNotification[];
    feed?: ReturnType<typeof createMockNotificationFeed>;
  } = {},
) {
  const feed = options.feed ?? createMockNotificationFeed({ seed: options.notifications ?? [] });
  const handlers = {
    onOpenSession: vi.fn(),
    onNewSession: vi.fn(),
    onArchived: vi.fn(),
  };
  const service = createMockVolundrService();
  service.updateProject = async (project, changes) => ({
    ...project,
    ...changes,
    revision: (project.revision ?? 1) + 1,
  });
  renderWithVolundr(
    <ProjectHome
      projectId="p-1"
      name="A2A Interoperability"
      project={PROJECT}
      sessions={SESSIONS}
      workerCounts={WORKER_COUNTS}
      {...handlers}
      {...props}
    />,
    { notifications: feed, service },
  );
  return handlers;
}

describe('ProjectHome', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(NOW);
    navigate.mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('heads the page with the project, New session and Settings', () => {
    const handlers = renderHome();
    expect(
      screen.getByRole('heading', { level: 1, name: 'A2A Interoperability' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'New session' }));
    expect(handlers.onNewSession).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('searchbox', { name: 'Search A2A Interoperability' })).toHaveAttribute(
      'placeholder',
      'Search A2A Interoperability',
    );
  });

  it('lists what needs the person, the coordinator and the other live sessions from session data', () => {
    const handlers = renderHome();

    const needs = screen.getByRole('region', { name: 'Needs you' });
    expect(within(needs).getByRole('heading')).toHaveTextContent('Needs you1');
    const attention = within(needs).getByTestId('project-attention-waiting');
    expect(attention).toHaveTextContent('Waiting');
    expect(attention).toHaveTextContent('waiting · Valhalla');
    expect(attention).toHaveTextContent('6h');
    expect(attention).toHaveTextContent('Delivery blocked: approve or send it back');

    const coordinator = screen.getByRole('region', { name: 'Coordinator' });
    const lead = within(coordinator).getByTestId('project-session-lead');
    expect(lead).toHaveTextContent('lead');
    expect(lead).toHaveTextContent('GPT-5.6 Terra · Valhalla · 5 workers');
    expect(lead).toHaveTextContent('Error');
    expect(lead).toHaveTextContent('failed 6h');

    // Sessions holds the other live ones, including the one that needs the person.
    const sessions = screen.getByRole('region', { name: 'Sessions' });
    expect(within(sessions).getByTestId('project-session-waiting')).toHaveTextContent('Needs you');
    expect(within(sessions).queryByTestId('project-session-lead')).not.toBeInTheDocument();

    // Another project's session, an archived one and a stopped one are not listed here.
    expect(screen.queryByTestId('project-session-elsewhere')).not.toBeInTheDocument();
    expect(screen.queryByTestId('project-session-archived-one')).not.toBeInTheDocument();
    expect(screen.queryByTestId('project-session-old-run')).not.toBeInTheDocument();

    fireEvent.click(attention);
    fireEvent.click(lead);
    expect(handlers.onOpenSession.mock.calls).toEqual([['waiting'], ['lead']]);
  });

  it('says "is waiting for you" when the session wrote no summary, and names plural coordinators', () => {
    renderHome({
      sessions: [
        makeSession({
          id: 'a',
          state: 'awaiting_input',
          activityStateSince: null,
          lastActivityAt: undefined,
        }),
        makeSession({
          id: 'c1',
          state: 'idle',
          coordination: { projectId: 'p-1', role: 'coordinator' },
        }),
        makeSession({
          id: 'c2',
          state: 'running',
          coordination: { projectId: 'p-1', role: 'coordinator' },
        }),
      ],
      workerCounts: new Map([['c2', 1]]),
    });
    expect(screen.getByTestId('project-attention-a')).toHaveTextContent('a is waiting for you');
    const coordinators = screen.getByRole('region', { name: 'Coordinators' });
    expect(within(coordinators).getByTestId('project-session-c1')).toHaveTextContent(
      'GPT-5.6 Terra · Valhalla',
    );
    expect(within(coordinators).getByTestId('project-session-c2')).toHaveTextContent('1 worker');
  });

  it('keeps stopped sessions behind a count until they are shown', () => {
    const handlers = renderHome();
    expect(screen.getByText('1 stopped')).toBeInTheDocument();
    const toggle = screen.getByTestId('project-stopped-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent('Hide');
    fireEvent.click(screen.getByTestId('project-session-old-run'));
    expect(handlers.onOpenSession).toHaveBeenCalledWith('old-run');
    fireEvent.click(toggle);
    expect(screen.queryByTestId('project-session-old-run')).not.toBeInTheDocument();
  });

  it('says "No sessions yet" for an empty project and shows no sections', () => {
    renderHome({ sessions: [] });
    expect(screen.getByText('No sessions yet')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Sessions' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Needs you' })).not.toBeInTheDocument();
  });

  it('filters every section by session name and says when nothing matches', () => {
    renderHome(
      {},
      {
        notifications: [
          makeNotification({ id: 'n1', seq: 1, sessionId: 'lead', sessionName: 'lead' }),
        ],
      },
    );
    const search = screen.getByRole('searchbox', { name: 'Search A2A Interoperability' });
    fireEvent.change(search, { target: { value: 'LEA' } });
    expect(screen.getByTestId('project-session-lead')).toBeInTheDocument();
    expect(screen.queryByTestId('project-session-waiting')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Needs you' })).not.toBeInTheDocument();
    expect(screen.queryByText('1 stopped')).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: 'zzz' } });
    expect(screen.getByText('No session matches “zzz”')).toBeInTheDocument();
    expect(screen.queryByTestId('project-session-lead')).not.toBeInTheDocument();

    fireEvent.change(search, { target: { value: 'old' } });
    expect(screen.getByText('1 stopped')).toBeInTheDocument();
    expect(screen.queryByTestId('project-session-lead')).not.toBeInTheDocument();
  });

  describe('Updates', () => {
    it('lists this project’s feed with open and resolved asks, and opens the session', async () => {
      const handlers = renderHome(
        {},
        {
          notifications: [
            makeNotification({
              id: 'ask-now',
              seq: 1,
              kind: 'decision',
              title: 'Delivery blocked',
              sessionId: 'waiting',
              sessionName: 'waiting',
            }),
            makeNotification({
              id: 'ask-before',
              seq: 24,
              kind: 'attention',
              title: 'Plan review',
              sessionId: 'waiting',
              sessionName: 'waiting',
            }),
            makeNotification({
              id: 'ask-done',
              seq: 5,
              kind: 'decision',
              title: 'Pick a branch',
              sessionId: 'lead',
              sessionName: 'lead',
            }),
            makeNotification({
              id: 'done',
              seq: 3,
              kind: 'milestone',
              title: 'Tests green',
              sessionId: 'lead',
              sessionName: 'lead',
            }),
            makeNotification({
              id: 'gone',
              seq: 8,
              title: 'From a deleted session',
              sessionId: 'deleted',
              sessionName: 'deleted',
            }),
            makeNotification({
              id: 'other-project',
              seq: 2,
              projectId: 'other',
              title: 'Not ours',
            }),
          ],
        },
      );
      const updates = await screen.findByRole('region', { name: 'Updates' });
      const rows = await within(updates).findAllByTestId('project-update');
      expect(rows.map((row) => row.textContent)).toEqual([
        expect.stringContaining('Delivery blocked'),
        expect.stringContaining('Tests green'),
        expect.stringContaining('Pick a branch'),
        expect.stringContaining('From a deleted session'),
        expect.stringContaining('Plan review'),
      ]);
      expect(within(updates).queryByText('Not ours')).not.toBeInTheDocument();
      expect(rows[0]).toHaveTextContent('Decision');
      expect(rows[0]).toHaveTextContent('1h');
      expect(rows[0]).not.toHaveAttribute('data-resolved');
      // The older ask of a session that is still waiting is superseded; an ask of a session
      // that stopped waiting is resolved. A notice that asks nothing is neither.
      expect(rows[2]).toHaveAttribute('data-resolved', 'true');
      expect(rows[2]).toHaveTextContent('Resolved');
      expect(rows[4]).toHaveAttribute('data-resolved', 'true');
      expect(rows[1]).not.toHaveAttribute('data-resolved');
      expect(rows[1]).not.toHaveTextContent('Resolved');

      fireEvent.click(within(rows[0]!).getByRole('button'));
      expect(handlers.onOpenSession).toHaveBeenCalledWith('waiting');
      // A session the project no longer lists cannot be opened from its update.
      expect(within(rows[3]!).queryByRole('button')).not.toBeInTheDocument();

      fireEvent.click(within(updates).getByRole('button', { name: 'All updates ›' }));
      expect(navigate).toHaveBeenCalledWith({ to: '/volundr/notifications' });
    });

    it('narrows the updates with the search', async () => {
      renderHome(
        {},
        {
          notifications: [
            makeNotification({
              id: 'a',
              seq: 1,
              title: 'Delivery blocked',
              sessionName: 'waiting',
            }),
            makeNotification({ id: 'b', seq: 2, title: 'Tests green', sessionName: 'lead' }),
          ],
        },
      );
      const updates = await screen.findByRole('region', { name: 'Updates' });
      await within(updates).findAllByTestId('project-update');
      fireEvent.change(screen.getByTestId('project-search'), { target: { value: 'tests' } });
      expect(within(updates).getAllByTestId('project-update')).toHaveLength(1);
      fireEvent.change(screen.getByTestId('project-search'), { target: { value: 'waiting' } });
      expect(within(updates).getAllByTestId('project-update')).toHaveLength(1);
      expect(within(updates).getByText('Delivery blocked')).toBeInTheDocument();
    });

    it('hides the section when the project has no updates', async () => {
      renderHome();
      await waitFor(() => expect(screen.queryByText('Loading updates…')).not.toBeInTheDocument());
      expect(screen.queryByRole('region', { name: 'Updates' })).not.toBeInTheDocument();
    });

    it('says while the feed loads', () => {
      const feed = createMockNotificationFeed();
      feed.list = () => new Promise(() => undefined);
      renderHome({}, { feed });
      expect(screen.getByRole('status')).toHaveTextContent('Loading updates…');
    });

    it('says when the feed could not be loaded and retries on request', async () => {
      const feed = createMockNotificationFeed({
        seed: [makeNotification({ id: 'n', seq: 1, title: 'Back again' })],
      });
      const list = feed.list.bind(feed);
      let calls = 0;
      feed.list = async (...args) => {
        calls += 1;
        if (calls === 1) throw new Error('Guild facade timed out');
        return list(...args);
      };
      renderHome({}, { feed });
      const alert = await screen.findByTestId('project-updates-error');
      expect(alert).toHaveTextContent('Updates could not be loaded. Guild facade timed out');
      expect(screen.getByRole('region', { name: 'Updates' })).toBeInTheDocument();
      fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('Back again')).toBeInTheDocument();
      expect(screen.queryByTestId('project-updates-error')).not.toBeInTheDocument();
    });
  });

  describe('Settings', () => {
    it('opens the settings dialog for the project', async () => {
      renderHome();
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Settings…' }));
      expect(await screen.findByRole('dialog', { name: 'Project settings' })).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('A2A Interoperability');
    });

    it('leaves the project when it is archived from the dialog', async () => {
      const handlers = renderHome();
      fireEvent.click(screen.getByRole('button', { name: 'Settings…' }));
      await screen.findByRole('dialog', { name: 'Project settings' });
      fireEvent.click(screen.getByRole('button', { name: 'Archive project' }));
      fireEvent.click(screen.getByRole('button', { name: 'Archive it' }));
      await waitFor(() => expect(handlers.onArchived).toHaveBeenCalledTimes(1));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('cannot change a project the host has no record of', () => {
      renderHome({ project: undefined });
      const settings = screen.getByRole('button', { name: 'Settings…' });
      expect(settings).toBeDisabled();
      expect(settings).toHaveAttribute('title', expect.stringContaining('no record'));
    });
  });
});

describe('resolvedAsks', () => {
  const awaiting = [makeSession({ id: 's', state: 'awaiting_input' })];
  const ask = (id: string, seq: number, sessionId: string | null = 's') =>
    makeNotification({ id, seq, kind: 'decision', sessionId });

  it('keeps only the newest ask of a waiting session open', () => {
    const newest = ask('new', 1);
    const older = ask('old', 2);
    expect([...resolvedAsks([newest, older], awaiting)]).toEqual(['valhalla:old']);
  });

  it('resolves every ask of a session that no longer waits and ignores notices and unbound asks', () => {
    const idle = [makeSession({ id: 's', state: 'idle' })];
    const notice = makeNotification({ id: 'info', seq: 3 });
    expect([...resolvedAsks([ask('a', 1), notice, ask('floating', 4, null)], idle)]).toEqual([
      'valhalla:a',
    ]);
  });
});
