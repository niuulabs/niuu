import { useEffect } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import type { Worker } from '@niuulabs/ui';
import { SessionsPage } from './SessionsPage';
import type { RoomWorkerFocus } from './LiveSessionDetailPage';
import {
  createMockPtyStream,
  createMockFileSystemPort,
  createMockVolundrService,
} from '../adapters/mock';
import type { ISessionStore } from '../ports/ISessionStore';
import type { Session } from '../domain/session';
import type { SessionMembers } from '../models/volundr.model';

const navigate = vi.fn();
let routeSessionId = 'team';
let roomWorkers: Worker[] = [];

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ sessionId: routeSessionId }),
}));

vi.mock('./LiveSessionDetailPage', () => ({
  LiveSessionDetailPage: ({
    sessionId,
    focusedWorkerId,
    onRoomWorkers,
  }: { sessionId: string } & RoomWorkerFocus) => {
    useEffect(() => {
      onRoomWorkers?.(roomWorkers);
    }, [onRoomWorkers]);
    return (
      <div data-testid="detail">
        {sessionId}:{focusedWorkerId ?? 'everyone'}
      </div>
    );
  },
}));

const worker = (peerId: string, name: string, status: Worker['status']): Worker => ({
  peerId,
  name,
  persona: name,
  color: '#3366aa',
  status,
  consumes: [],
  emits: [],
  outcomes: [],
});

const session = (id: string, name: string, extra: Partial<Session> = {}): Session => ({
  id,
  name,
  ravnId: `ravn-${id}`,
  personaName: name,
  templateId: 'tpl',
  clusterId: 'cluster-a',
  state: 'running',
  startedAt: '2026-11-05T05:00:00.000Z',
  lastActivityAt: '2026-11-05T05:05:00.000Z',
  resources: {
    cpuRequest: 1,
    cpuLimit: 1,
    cpuUsed: 0,
    memRequestMi: 1,
    memLimitMi: 1,
    memUsedMi: 0,
    gpuCount: 0,
  },
  env: {},
  events: [],
  ...extra,
});

const team = (extra: Partial<Session> = {}) =>
  session('team', 'Fix SSO token refresh', {
    team: { taskTo: 'lead' },
    peopleCount: 2,
    state: 'idle',
    ...extra,
  });
const member = (id: string, name: string, state: Session['state'], lead = false) =>
  session(id, name, { hostedBy: 'team', teamMember: { lead }, state });

const MEMBERS: SessionMembers = {
  agents: [
    {
      sessionId: 'm1',
      name: 'team-coordinator',
      personaName: 'Coordinator',
      model: 'claude-sonnet',
      definition: 'skuldClaude',
      status: 'running',
      lead: true,
    },
    {
      sessionId: 'm2',
      name: 'team-coder',
      personaName: 'Coder',
      model: 'gpt-codex',
      definition: 'skuldCodex',
      status: 'running',
      lead: false,
    },
  ],
  people: [
    { userId: 'u1', displayName: 'Dev', role: 'owner', status: 'active', online: true },
    { userId: 'sam', displayName: 'Sam K.', role: 'viewer', status: 'invited', online: null },
  ],
};

function renderPage(
  sessions: Session[],
  members: SessionMembers | Error = MEMBERS,
  options: { onMembers?: (id: string) => void } = {},
) {
  const store: ISessionStore = {
    getSession: async (id) => sessions.find((s) => s.id === id) ?? null,
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
  const volundr = createMockVolundrService();
  volundr.getSessionMembers = async (id) => {
    options.onMembers?.(id);
    if (members instanceof Error) throw members;
    return members;
  };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          'niuu.repos': { getRepos: volundr.getRepos.bind(volundr) },
          sessionStore: store,
          ptyStream: createMockPtyStream(),
          filesystem: createMockFileSystemPort(),
        }}
      >
        <SessionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  navigate.mockClear();
  roomWorkers = [];
  routeSessionId = 'team';
});

describe('SessionsPage — a team in the list', () => {
  const sessions = () => [
    team(),
    member('m1', 'team-coordinator', 'idle', true),
    member('m2', 'team-coder', 'running'),
    session('solo', 'Bump the catalog'),
  ];

  it('folds the members into one row that says who is in it', async () => {
    renderPage(sessions());

    expect(await screen.findByTestId('session-unit-team')).toHaveTextContent('2 agents · 2 people');
    // One dot per agent and per person, and no worker count where the team's own count is.
    expect(screen.getByTestId('session-stack-team').children).toHaveLength(4);
    expect(screen.queryByTestId('session-worker-count-team')).toBeNull();
    expect(screen.queryByTestId('session-leads-team')).toBeNull();
    // The members are the team's agents, not sessions of their own in the list.
    expect(screen.queryByTestId('pod-entry-m1')).toBeNull();
    expect(screen.queryByTestId('pod-entry-m2')).toBeNull();
  });

  it('lists the agents with their persona, engine and who leads, then the people', async () => {
    renderPage(sessions());

    const coordinator = await screen.findByTestId('session-agent-m1');
    expect(coordinator).toHaveTextContent('Coordinator');
    expect(coordinator).toHaveTextContent('Claude Code');
    expect(coordinator).toHaveTextContent('Leads');
    expect(coordinator).toHaveTextContent('Idle');
    const coder = screen.getByTestId('session-agent-m2');
    expect(coder).toHaveTextContent('Coder');
    expect(coder).toHaveTextContent('Codex');
    expect(coder).toHaveTextContent('Working');
    expect(coder).not.toHaveTextContent('Leads');

    const owner = screen.getByTestId('session-person-u1');
    expect(owner).toHaveTextContent('You');
    expect(owner).toHaveTextContent('owner');
    expect(owner).toHaveTextContent('Online');
    const invited = screen.getByTestId('session-person-sam');
    expect(invited).toHaveTextContent('Sam K.');
    expect(invited).toHaveTextContent('invited');
    expect(invited).not.toHaveTextContent('Online');
    expect(invited).not.toHaveTextContent('Offline');
    // A person is shown, not opened.
    expect(invited.tagName).toBe('DIV');
  });

  it('opens an agent’s own session when its row is picked', async () => {
    renderPage(sessions());
    fireEvent.click(await screen.findByTestId('session-agent-m2'));
    expect(navigate).toHaveBeenCalledWith({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: 'm2' },
    });
  });

  it('says what the host last said about an agent that is not in the list', async () => {
    renderPage([team(), session('solo', 'Bump the catalog')], {
      ...MEMBERS,
      agents: [{ ...MEMBERS.agents[0]!, status: 'starting' }],
      people: [{ ...MEMBERS.people[0]!, online: false }],
    });
    expect(await screen.findByTestId('session-agent-m1')).toHaveTextContent('Starting');
    expect(screen.getByTestId('session-person-u1')).toHaveTextContent('Offline');
  });

  it('shows a failed agent as an error', async () => {
    renderPage([team(), session('solo', 'Bump the catalog')], {
      ...MEMBERS,
      agents: [{ ...MEMBERS.agents[0]!, status: 'error' }],
    });
    const row = await screen.findByTestId('session-agent-m1');
    expect(row).toHaveTextContent('Error');
    expect(within(row).getByText('Error')).toHaveAttribute('data-status', 'error');
  });

  it('says so when the members cannot be read, naming the cause', async () => {
    renderPage(sessions(), new Error('the room did not answer'));
    expect(await screen.findByRole('alert')).toHaveTextContent('the room did not answer');
    expect(screen.queryByTestId('session-agent-m1')).toBeNull();
  });

  it('folds the members away and asks again only when they are shown', async () => {
    const asked: string[] = [];
    routeSessionId = 'solo';
    renderPage(sessions(), MEMBERS, { onMembers: (id) => asked.push(id) });
    await screen.findByTestId('session-agent-m1');
    expect(asked).toEqual(['team']);

    const entry = screen.getByTestId('pod-entry-team').closest('.forge-session-tree-entry')!;
    fireEvent.click(
      within(entry as HTMLElement).getByRole('button', { name: /Collapse members of/ }),
    );
    expect(screen.queryByTestId('session-agent-m1')).toBeNull();
    // The summary stays on the folded row.
    expect(screen.getByTestId('session-unit-team')).toBeInTheDocument();
  });

  it('counts a team with only its owner in it as "you", and as one person to a participant', async () => {
    renderPage([
      team({ peopleCount: 1 }),
      member('m1', 'team-coordinator', 'idle', true),
      member('m2', 'team-coder', 'running'),
    ]);
    expect(await screen.findByTestId('session-unit-team')).toHaveTextContent('2 agents · you');
  });

  it('counts a shared team’s owner as one person to the person it is shared with', async () => {
    renderPage([
      team({
        peopleCount: 1,
        room: { role: 'viewer', participantStatus: 'active', expiresAt: null },
      }),
      member('m1', 'team-coordinator', 'idle', true),
    ]);
    expect(await screen.findByTestId('session-unit-team')).toHaveTextContent('1 agent · 1 person');
  });
});

describe('SessionsPage — the open team’s room', () => {
  it('lists an agent as the room’s worker, and shows only its output when picked', async () => {
    roomWorkers = [worker('m1', 'Coordinator', 'idle'), worker('m2', 'Coder', 'working')];
    renderPage([
      team(),
      member('m1', 'team-coordinator', 'idle', true),
      member('m2', 'team-coder', 'running'),
    ]);

    const coder = await screen.findByTestId('room-worker-m2');
    expect(coder).toHaveTextContent('Coder');
    expect(coder).toHaveTextContent('Codex');
    expect(coder).toHaveTextContent('Working');
    expect(screen.getByTestId('room-worker-m1')).toHaveTextContent('Leads');
    // Each agent is one row: the room's, not also a session row.
    expect(screen.queryByTestId('session-agent-m2')).toBeNull();

    fireEvent.click(coder);
    expect(screen.getByTestId('detail')).toHaveTextContent('team:m2');
    expect(screen.getByTestId('room-worker-m2')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('pod-entry-team')).not.toHaveAttribute('aria-current');

    fireEvent.click(screen.getByTestId('pod-entry-team'));
    expect(screen.getByTestId('detail')).toHaveTextContent('team:everyone');
  });

  it('keeps a room worker that is not one of the team’s agents', async () => {
    roomWorkers = [worker('m1', 'Coordinator', 'idle'), worker('stray', 'Observer', 'idle')];
    renderPage([team(), member('m1', 'team-coordinator', 'idle', true)]);
    expect(await screen.findByTestId('room-worker-stray')).toHaveTextContent('Observer');
  });

  it('leaves the team’s list where it is when one of its members is opened', async () => {
    routeSessionId = 'm1';
    roomWorkers = [worker('m2', 'Coder', 'working')];
    renderPage([
      team(),
      member('m1', 'team-coordinator', 'idle', true),
      member('m2', 'team-coder', 'running'),
    ]);

    expect(await screen.findByTestId('detail')).toHaveTextContent('m1:everyone');
    // The member's chat names its peers, but it leads none of them.
    expect(await screen.findByTestId('session-agent-m2')).toBeInTheDocument();
    expect(screen.queryByTestId('room-worker-m2')).toBeNull();
  });
});

describe('SessionsPage — a session someone else joined', () => {
  it('reads as it did, with the people behind a fold', async () => {
    const asked: string[] = [];
    routeSessionId = 'other';
    renderPage(
      [session('solo', 'Chart the sidecar', { peopleCount: 3 }), session('other', 'Other')],
      MEMBERS,
      { onMembers: (id) => asked.push(id) },
    );

    expect(await screen.findByTestId('session-unit-solo')).toHaveTextContent('1 agent · 3 people');
    expect(screen.queryByTestId('session-person-sam')).toBeNull();
    expect(asked).toEqual([]);

    const entry = screen.getByTestId('pod-entry-solo').closest('.forge-session-tree-entry')!;
    fireEvent.click(
      within(entry as HTMLElement).getByRole('button', { name: /Expand members of/ }),
    );
    expect(await screen.findByTestId('session-person-sam')).toBeInTheDocument();
    // The session works alone, so its own agent is listed with the people.
    await waitFor(() => expect(asked).toEqual(['solo']));
    expect(screen.getByTestId('session-agent-m1')).toBeInTheDocument();
  });

  it('shows no unit on a session nobody else is in', async () => {
    routeSessionId = 'solo';
    renderPage([session('solo', 'Bump the catalog', { peopleCount: 1 }), session('b', 'B')]);
    await screen.findByTestId('pod-entry-solo');
    expect(screen.queryByTestId('session-unit-solo')).toBeNull();
    expect(
      screen
        .getByTestId('pod-entry-solo')
        .closest('.forge-session-tree-entry')
        ?.querySelector('button.forge-session-tree-toggle'),
    ).toBeNull();
  });

  it('keeps a coordinator’s workers and adds only its people', async () => {
    routeSessionId = 'other';
    renderPage(
      [
        session('lead', 'Coordinator', {
          peopleCount: 2,
          coordination: { projectId: 'p', role: 'coordinator', parent: null },
        }),
        session('w1', 'Worker', {
          coordination: {
            projectId: 'p',
            role: 'worker',
            parent: { instanceId: 'cluster-a', sessionId: 'lead' },
          },
        }),
        session('other', 'Other'),
      ],
      MEMBERS,
    );

    expect(await screen.findByTestId('session-unit-lead')).toHaveTextContent(/^2 people$/);
    expect(screen.getByTestId('session-leads-lead')).toHaveTextContent('Coordinator');
    expect(screen.getByTestId('pod-entry-w1')).toBeInTheDocument();
    // The people follow the workers; the coordinator's own row is its agent, not repeated.
    expect(await screen.findByTestId('session-person-sam')).toBeInTheDocument();
    expect(screen.queryByTestId('session-agent-m1')).toBeNull();
  });
});
