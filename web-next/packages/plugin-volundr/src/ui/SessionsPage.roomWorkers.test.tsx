import { useEffect } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
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

const navigate = vi.fn();

let routeSessionId = 'lead';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useParams: () => ({ sessionId: routeSessionId }),
}));

const worker = (
  peerId: string,
  name: string,
  status: Worker['status'],
  persona: string = name,
): Worker => ({
  peerId,
  name,
  persona,
  color: '#3366aa',
  status,
  consumes: [],
  emits: [],
  outcomes: [],
});

// The open session's chat reports its room; the list shows that room under the session.
vi.mock('./LiveSessionDetailPage', () => ({
  LiveSessionDetailPage: ({
    sessionId,
    focusedWorkerId,
    onRoomWorkers,
  }: { sessionId: string } & RoomWorkerFocus) => {
    useEffect(() => {
      onRoomWorkers?.([
        worker('coder', 'coder', 'working'),
        worker('rev', 'wf-run-reviewer', 'idle', 'reviewer'),
      ]);
    }, [onRoomWorkers]);
    return (
      <div data-testid="detail">
        {sessionId}:{focusedWorkerId ?? 'everyone'}
      </div>
    );
  },
}));

const session = (id: string, name: string): Session => ({
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
});

function renderPage(extra: Session[] = []) {
  const sessions = [session('lead', 'storm-probe'), session('other', 'other-session'), ...extra];
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

describe('SessionsPage — the open room’s workers', () => {
  it('lists the room’s workers under the session, which reads as their coordinator', async () => {
    renderPage();
    const coder = await screen.findByTestId('room-worker-coder');
    expect(coder).toHaveTextContent('coder');
    expect(coder).toHaveTextContent('Working');
    expect(screen.getByTestId('room-worker-rev')).toHaveTextContent('Idle');
    expect(screen.getByTestId('session-leads-lead')).toHaveTextContent('Coordinator · 2 workers');
    expect(screen.queryByTestId('session-leads-other')).toBeNull();
  });

  it('shows one worker on its own when picked, and everyone again from the coordinator', async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId('room-worker-rev'));
    expect(screen.getByTestId('detail')).toHaveTextContent('lead:rev');
    expect(screen.getByTestId('room-worker-rev')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('pod-entry-lead')).not.toHaveAttribute('aria-current');

    fireEvent.click(screen.getByTestId('pod-entry-lead'));
    expect(screen.getByTestId('detail')).toHaveTextContent('lead:everyone');
    expect(screen.getByTestId('pod-entry-lead')).toHaveAttribute('aria-current', 'true');
  });

  it('folds the workers away with the coordinator', async () => {
    renderPage();
    await screen.findByTestId('room-worker-coder');
    const entry = screen.getByTestId('pod-entry-lead').closest('.forge-session-tree-entry')!;
    fireEvent.click(
      within(entry as HTMLElement).getByRole('button', { name: /Collapse workers of/ }),
    );
    expect(screen.queryByTestId('room-worker-coder')).toBeNull();
  });

  it("lists a workflow run's member sessions as its room's workers, counted once", async () => {
    // An earlier case folds the lead; this one reads it open.
    localStorage.clear();
    const member = (id: string, role: string): Session => ({
      ...session(id, `wf-run-${role}`),
      coordination: {
        projectId: 'probe',
        role,
        parent: { instanceId: 'cluster-a', sessionId: 'lead' },
      },
    });
    renderPage([member('coder', 'coder'), member('rev', 'reviewer')]);

    await screen.findByTestId('room-worker-coder');
    expect(screen.getByTestId('room-worker-rev')).toBeInTheDocument();
    // Each member is listed once, as the room's worker, not again as a session row.
    expect(screen.queryByTestId('pod-entry-coder')).toBeNull();
    expect(screen.queryByTestId('pod-entry-rev')).toBeNull();
    expect(screen.getByTestId('session-leads-lead')).toHaveTextContent('2 workers');
  });

  it("leads a run member's row with its role, ahead of its long name", async () => {
    localStorage.clear();
    const member = (id: string, role: string): Session => ({
      ...session(id, `wf-run-0123456789ab-${role}`),
      coordination: {
        projectId: 'probe',
        role,
        parent: { instanceId: 'cluster-a', sessionId: 'lead' },
      },
    });
    routeSessionId = 'coder';
    try {
      renderPage([member('coder', 'coder'), member('rev', 'security-auditor')]);

      expect(await screen.findByTestId('pod-entry-rev-role')).toHaveTextContent('Security-auditor');
      expect(screen.getByTestId('pod-entry-coder-role')).toHaveTextContent('Coder');
      // One line, as under the open run: the state ends the line, no second line under it.
      const row = screen.getByTestId('pod-entry-coder');
      expect(within(row).getByTestId('pod-entry-coder-state')).toBeInTheDocument();
      expect(row.querySelector('.forge-session-row__metadata')).toBeNull();
      // The run's own session has no parent: it is named, not given a role.
      expect(screen.queryByTestId('pod-entry-lead-role')).toBeNull();
    } finally {
      routeSessionId = 'lead';
    }
  });

  it("leads an open room's worker row with its persona", async () => {
    localStorage.clear();
    renderPage();
    expect(await screen.findByTestId('room-worker-rev-role')).toHaveTextContent('Reviewer');
    // A worker whose persona is its name says it once.
    expect(screen.queryByTestId('room-worker-coder-role')).toBeNull();
  });

  it("leaves the run's sessions where they are when one of its members is opened", async () => {
    localStorage.clear();
    const member = (id: string, role: string): Session => ({
      ...session(id, `wf-run-${role}`),
      coordination: {
        projectId: 'probe',
        role,
        parent: { instanceId: 'cluster-a', sessionId: 'lead' },
      },
    });
    routeSessionId = 'coder';
    try {
      renderPage([member('coder', 'coder'), member('rev', 'reviewer')]);

      expect(await screen.findByTestId('detail')).toHaveTextContent('coder:everyone');
      // The member's chat names the run's other members, but it leads none of them.
      expect(screen.queryByTestId('room-worker-rev')).toBeNull();
      expect(screen.queryByTestId('session-leads-coder')).toBeNull();
      expect(screen.getByTestId('pod-entry-rev')).toBeInTheDocument();
    } finally {
      routeSessionId = 'lead';
    }
  });
});
