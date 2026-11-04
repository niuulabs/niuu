import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService } from '../../ports';
import type { WorkSummary } from '../../domain/work';
import type { ReviewItem } from '../../domain/review';
import type { Publication, SessionRef } from '../../domain/artifacts';
import { ArtifactList } from './ArtifactList';
import { ArtifactsPage } from './ArtifactsPage';

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    search,
    className,
  }: {
    children: React.ReactNode;
    to: string;
    search?: Record<string, string>;
    className?: string;
  }) => (
    <a
      href={`${to}${search ? `?${new URLSearchParams(search).toString()}` : ''}`}
      className={className}
      data-testid={className === 'ting-artifacts__row' ? 'artifact-row' : undefined}
    >
      {children}
    </a>
  ),
}));

function run(slug: string, status: string, condition: WorkSummary['condition']): WorkSummary {
  return {
    id: `campaign:${slug}`,
    kind: 'workflowLaunch',
    title: slug === 'a2a-plan' ? 'Developer Planning' : 'Tool build',
    campaignSlug: slug,
    condition,
    rawState: { source: 'campaign', status },
    currentStep: null,
    attention: null,
    createdAt: null,
    updatedAt: null,
    source: null,
    workflow: { id: 'wf', version: '1', documentRevision: null },
    session: { id: `session-${slug}`, connectionId: slug === 'a2a-plan' ? 'valhalla' : 'ymir' },
    links: { self: '', specialist: '' },
    actions: [],
  };
}

function presented(seq: number, sessionId: string, key: string, at: string): Publication {
  return {
    seq,
    sessionId,
    providerKey: `${sessionId}:${key}`,
    revisionId: `pf_${seq}`,
    fileId: `pf_${seq}`,
    title: `${key}.md`,
    url: null,
    at,
    mime: 'text/markdown',
    versionLabel: `v${seq}`,
  };
}

const PUBLICATIONS: Record<string, Publication[]> = {
  valhalla: [
    presented(1, 'session-a2a-plan', 'plan', '2026-11-02T23:00:00Z'),
    presented(2, 'session-a2a-plan', 'plan', '2026-11-02T23:10:00Z'),
    // A session Work does not list is not one of its runs.
    // A session that is no run, and one Forge no longer has: their files still list.
    presented(3, 'session-elsewhere', 'notes', '2026-11-03T12:00:00Z'),
    presented(4, 'session-gone', 'notes', '2026-11-01T09:00:00Z'),
  ],
  ymir: [presented(1, 'session-a2a-tool', 'research-report', '2026-11-03T10:00:00Z')],
};

const SESSIONS: Record<string, SessionRef | null> = {
  'session-elsewhere': {
    sessionId: 'session-elsewhere',
    instanceId: 'valhalla',
    title: 'Ad hoc notes',
    running: true,
  },
  'session-gone': null,
};

function setup({
  reviews = [],
  publications = vi.fn(async (host: string | null) => PUBLICATIONS[host ?? ''] ?? []),
  projects = [],
  projectId,
  page = false,
  session = vi.fn(async (sessionId: string) => SESSIONS[sessionId] ?? null),
}: {
  reviews?: ReviewItem[];
  publications?: IRunLogService['publications'];
  projects?: Awaited<ReturnType<IRunLogService['projectSessions']>>;
  projectId?: string;
  page?: boolean;
  session?: IRunLogService['session'];
} = {}) {
  const work = {
    list: vi.fn().mockResolvedValue({
      projects: [],
      campaigns: [
        run('a2a-plan', 'blocked', 'needsAttention'),
        run('a2a-tool', 'completed', 'completed'),
      ],
      executions: [],
      executionNextCursor: null,
      coverage: [],
    }),
    reviews: vi.fn().mockResolvedValue({ items: reviews }),
  } as unknown as IWorkService;
  const runLog = {
    hosts: vi.fn().mockResolvedValue(['valhalla', 'ymir']),
    session,
    publications,
    projectSessions: vi.fn().mockResolvedValue(projects),
    sendMessage: vi.fn(),
  } as unknown as IRunLogService;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider services={{ 'ting.work': work, 'ting.runLog': runLog }}>
        {page ? <ArtifactsPage /> : <ArtifactList projectId={projectId} />}
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { publications, session };
}

const rowTexts = () => screen.getAllByTestId('artifact-row').map((row) => row.textContent);

describe('ArtifactList', () => {
  it('lists what every session presented, newest first, and what waits on a person', async () => {
    setup({ page: true });

    expect(await screen.findByRole('heading', { name: 'Artifacts' })).toBeInTheDocument();
    const waiting = await screen.findByRole('region', { name: 'Waiting on you' });
    expect(within(waiting).getByText('Developer Planning')).toBeInTheDocument();
    const group = screen.getByRole('region', { name: 'No project' });
    await waitFor(() => expect(within(group).getAllByTestId('artifact-row')).toHaveLength(3));
    const rows = within(group).getAllByTestId('artifact-row');
    // What waits is listed once, above the projects, not again in its project.
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining('Ad hoc notes'),
      expect.stringContaining('Research report'),
      expect.stringContaining('Session session- (gone)'),
    ]);
    expect(rows[0]).toHaveTextContent('notes.md');
    expect(rows[0]).toHaveAttribute(
      'href',
      '/ting/artifacts/session/session-elsewhere?artifact=session-elsewhere%3Anotes&instance=valhalla',
    );
    const plan = within(waiting)
      .getByText(/plan\.md · v2 of 2 versions/)
      .closest('a');
    expect(plan).toHaveAttribute(
      'href',
      '/ting/artifacts/a2a-plan?artifact=session-a2a-plan%3Aplan',
    );
  });

  it('filters by status, kind and text', async () => {
    setup();
    await screen.findByRole('region', { name: 'No project' });

    fireEvent.click(screen.getByRole('button', { name: /^Done/ }));
    expect(rowTexts()).toEqual([
      expect.stringContaining('Research report'),
      expect.stringContaining('(gone)'),
    ]);
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Plan' } });
    expect(rowTexts().every((text) => text?.includes('Plan'))).toBe(true);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search artifacts' }), {
      target: { value: 'nothing like this' },
    });
    expect(screen.getByText('Nothing matches these filters.')).toBeInTheDocument();
  });

  it('keeps a gate that waits with nothing presented yet', async () => {
    setup({
      reviews: [
        {
          taskId: 'a2a-gate',
          kind: 'run',
          title: 'Infra change',
          workflowName: 'Infra Change Flow',
          state: 'waiting',
          gateLabel: 'Approve the merge',
          updatedAt: '2026-11-03T11:00:00Z',
        },
      ],
    });

    const waiting = await screen.findByRole('region', { name: 'Waiting on you' });
    const gate = within(waiting).getByText('Infra change').closest('a');
    expect(gate).toHaveAttribute('href', '/ting/artifacts/a2a-gate');
    expect(gate).toHaveTextContent('Decision');
  });

  it('keeps a run held at a gate that presented nothing and that no review names', async () => {
    setup({
      publications: vi.fn(async (host: string | null) =>
        host === 'valhalla' ? [] : (PUBLICATIONS[host ?? ''] ?? []),
      ),
    });

    const waiting = await screen.findByRole('region', { name: 'Waiting on you' });
    const row = within(waiting).getByText('Developer Planning').closest('a');
    expect(row).toHaveAttribute('href', '/ting/artifacts/a2a-plan');
    expect(row).toHaveTextContent('Waiting at a gate · nothing presented yet');
  });

  it('says which hosts and sessions could not be read, and why', async () => {
    setup({
      publications: vi.fn(async (host: string | null) => {
        if (host === 'ymir') throw new Error('Forge unavailable');
        return PUBLICATIONS[host ?? ''] ?? [];
      }),
      session: vi.fn(async (sessionId: string) => {
        if (sessionId === 'session-elsewhere') throw new Error('Session lookup failed');
        return null;
      }),
    });

    expect(await screen.findByText(/2 sources of artifacts could not be read/)).toBeInTheDocument();
    expect(screen.getByText('ymir: Forge unavailable')).toBeInTheDocument();
    expect(
      screen.getByText('Session session-elsewhere: Session lookup failed'),
    ).toBeInTheDocument();
    const waiting = screen.getByRole('region', { name: 'Waiting on you' });
    expect(within(waiting).getAllByTestId('artifact-row')[0]).toHaveTextContent('Plan');
    const group = screen.getByRole('region', { name: 'No project' });
    expect(
      within(group)
        .getAllByTestId('artifact-row')
        .map((row) => row.textContent),
    ).toEqual([expect.stringContaining('(gone)')]);
  });

  it("for one project lists only its own sessions' files", async () => {
    const { session } = setup({
      projectId: 'p1',
      projects: [{ projectId: 'p1', name: 'Yggdrasil', sessionIds: ['session-a2a-tool'] }],
    });

    await waitFor(() => expect(rowTexts()).toEqual([expect.stringContaining('Research report')]));
    expect(session).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: /Yggdrasil/ })).not.toBeInTheDocument();
  });

  it('groups sessions and runs by the project they belong to', async () => {
    setup({
      projects: [
        {
          projectId: 'p1',
          name: 'Yggdrasil',
          sessionIds: ['session-a2a-tool', 'session-elsewhere'],
        },
      ],
    });

    const group = await screen.findByRole('region', { name: 'Yggdrasil' });
    expect(within(group).getByText(/Research report/)).toBeInTheDocument();
    expect(within(group).getByText(/Ad hoc notes/)).toBeInTheDocument();
  });

  it('says when no session has presented anything yet', async () => {
    setup({
      publications: vi.fn().mockResolvedValue([]),
      projectId: 'p1',
      projects: [{ projectId: 'p1', name: 'Yggdrasil', sessionIds: ['session-a2a-tool'] }],
    });

    expect(await screen.findByText('No session has presented a document yet.')).toBeInTheDocument();
  });
});
