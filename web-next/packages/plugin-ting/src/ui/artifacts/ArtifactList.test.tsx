import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesProvider, UNAVAILABLE_SERVICE } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService } from '../../ports';
import type { WorkSummary } from '../../domain/work';
import type { ReviewItem } from '../../domain/review';
import type { Publication, SessionRef } from '../../domain/artifacts';
import { ArtifactList } from './ArtifactList';
import { ArtifactsPage } from './ArtifactsPage';
import { ShellContext, type ShellContextValue } from '@niuulabs/shell';

vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => '/here',
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
    // A run held at a gate asks for a decision, as Work reports it.
    attention:
      condition === 'needsAttention' ? { kind: 'decision', message: 'Waiting for input.' } : null,
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
  campaigns = [
    run('a2a-plan', 'blocked', 'needsAttention'),
    run('a2a-tool', 'completed', 'completed'),
  ],
  node,
  work: workOverride,
  pluginOff = false,
  filesystem,
}: {
  reviews?: ReviewItem[];
  publications?: IRunLogService['publications'];
  projects?: Awaited<ReturnType<IRunLogService['projectSessions']>>;
  projectId?: string;
  page?: boolean;
  session?: IRunLogService['session'];
  campaigns?: WorkSummary[];
  /** The one host a node-scoped UI covers. */
  node?: string;
  work?: unknown;
  pluginOff?: boolean;
  filesystem?: unknown;
} = {}) {
  const work = {
    list: vi.fn().mockResolvedValue({
      projects: [],
      campaigns,
      executions: [],
      executionNextCursor: null,
      coverage: [],
    }),
    reviews: vi.fn().mockResolvedValue({ items: reviews }),
  } as unknown as IWorkService;
  const runLog = {
    ...(node ? { scope: 'node' } : {}),
    hosts: vi.fn().mockResolvedValue(node ? [node] : ['valhalla', 'ymir']),
    session,
    publications,
    projectSessions: vi.fn().mockResolvedValue(projects),
    sendMessage: vi.fn(),
  } as unknown as IRunLogService;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider
        services={{
          'ting.work': workOverride ?? work,
          'ting.runLog': runLog,
          ...(filesystem ? { filesystem } : {}),
        }}
      >
        <ShellContext.Provider value={pluginOff ? SHELL_WITHOUT_TING : null}>
          {page ? <ArtifactsPage /> : <ArtifactList projectId={projectId} />}
        </ShellContext.Provider>
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { publications, session };
}

const SHELL_WITHOUT_TING: ShellContextValue = {
  enabled: [],
  brand: null,
  version: '0',
  ctx: { tweaks: {}, setTweak: () => {} },
};

function storedFiles(read: () => Promise<Blob>) {
  return { downloadPresentedFile: vi.fn(read) };
}

/** A host without Ting: anything asked of it throws. */
function absentTing() {
  const asked = vi.fn((method: string) => {
    throw new Error(`asked an absent Ting for ${method}`);
  });
  const service = new Proxy(
    { [UNAVAILABLE_SERVICE]: { available: false, serviceName: 'ting.work', reason: 'off' } },
    {
      get: (target, key) => (key in target ? Reflect.get(target, key) : () => asked(String(key))),
    },
  );
  return { service, asked };
}

/** The row of a file, once its host's files are read. */
const fileRow = (text: string) =>
  waitFor(() => {
    const found = screen
      .getAllByTestId('artifact-row')
      .find((row) => row.tagName === 'BUTTON' && row.textContent?.includes(text));
    expect(found).toBeDefined();
    return found!;
  });

const rowTexts = () => screen.getAllByTestId('artifact-row').map((row) => row.textContent);

describe('ArtifactList', () => {
  it("lists one node's files once, whatever other hosts Ting knows runs on", async () => {
    const publications = vi.fn(async () => PUBLICATIONS.valhalla!);
    const { session } = setup({
      node: 'valhalla',
      publications,
      campaigns: [
        run('a2a-plan', 'completed', 'completed'),
        run('a2a-tool', 'completed', 'completed'),
        // A step-engine run names no session, and so no host.
        { ...run('a2a-steps', 'running', 'active'), session: null },
      ],
    });

    await waitFor(() => expect(rowTexts()).toHaveLength(3));
    expect(rowTexts()).toEqual([
      expect.stringContaining('Ad hoc notes'),
      expect.stringContaining('Developer Planning'),
      expect.stringContaining('(gone)'),
    ]);
    expect(new Set(publications.mock.calls.map(([host]: unknown[]) => host))).toEqual(
      new Set(['valhalla']),
    );
    expect(session).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('lists what sessions presented on a host without Ting, asking Ting nothing', async () => {
    const { service, asked } = absentTing();
    setup({ node: 'valhalla', work: service });

    // Forge names no run here, so the session that presented the plan is looked up too.
    await waitFor(() => expect(rowTexts()).toHaveLength(3));
    expect(asked).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText('Reading artifacts…')).not.toBeInTheDocument();
  });

  it('opens the newest file itself where the Ting plugin is off', async () => {
    const filesystem = storedFiles(async () => new Blob(['# The plan'], { type: 'text/markdown' }));
    setup({
      node: 'valhalla',
      pluginOff: true,
      filesystem,
      campaigns: [run('a2a-plan', 'completed', 'completed')],
    });

    const row = await fileRow('Developer Planning');
    expect(row.tagName).toBe('BUTTON');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    fireEvent.click(row);

    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('The plan')).toBeInTheDocument();
    // Forge's stored copy, from the session that presented it.
    expect(filesystem.downloadPresentedFile).toHaveBeenCalledExactlyOnceWith(
      'session-a2a-plan',
      'pf_2',
      expect.anything(),
    );
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('opens the file on a host without Ting, whose plugin has nothing to show', async () => {
    const filesystem = storedFiles(async () => new Blob(['notes'], { type: 'text/markdown' }));
    setup({ node: 'valhalla', work: absentTing().service, filesystem });

    fireEvent.click(await fileRow('Ad hoc notes'));
    expect(await within(await screen.findByRole('dialog')).findByText('notes')).toBeInTheDocument();
  });

  it('shows why a file cannot be read', async () => {
    const filesystem = storedFiles(async () => {
      throw new Error('Session session-gone was deleted');
    });
    setup({ node: 'valhalla', pluginOff: true, filesystem });

    fireEvent.click(await fileRow('(gone)'));
    expect(await within(await screen.findByRole('dialog')).findByRole('alert')).toHaveTextContent(
      'Session session-gone was deleted',
    );
  });

  it('says when the app has no file browser to open one with', async () => {
    setup({ node: 'valhalla', pluginOff: true });
    fireEvent.click(await fileRow('Ad hoc notes'));
    expect(await screen.findByText('This app has no file browser connected.')).toBeInTheDocument();
  });

  it('lists a decision with nothing presented as a plain line where its page is not here', async () => {
    setup({
      pluginOff: true,
      publications: vi.fn(async () => []),
      campaigns: [run('a2a-plan', 'blocked', 'needsAttention')],
    });
    const row = await screen.findByTestId('artifact-row');
    expect(row.tagName).toBe('DIV');
    expect(row).toHaveTextContent('Waiting at a gate');
  });

  it('shows the error of a configured Ting that fails, 404 or not', async () => {
    const work = {
      list: vi.fn().mockRejectedValue(new Error('API request failed: 404')),
      reviews: vi.fn().mockRejectedValue(new Error('API request failed: 404')),
    };
    setup({ node: 'valhalla', work });

    expect(await screen.findByRole('alert')).toHaveTextContent('API request failed: 404');
  });

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
      '/ting/artifacts/session/session-elsewhere?artifact=session-elsewhere%3Anotes&instance=valhalla&returnTo=%2Fhere',
    );
    const plan = within(waiting)
      .getByText(/plan\.md · v2 of 2 versions/)
      .closest('a');
    expect(plan).toHaveAttribute(
      'href',
      '/ting/artifacts/a2a-plan?artifact=session-a2a-plan%3Aplan&returnTo=%2Fhere',
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
    expect(gate).toHaveAttribute('href', '/ting/artifacts/a2a-gate?returnTo=%2Fhere');
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
    expect(row).toHaveAttribute('href', '/ting/artifacts/a2a-plan?returnTo=%2Fhere');
    expect(row).toHaveTextContent('Waiting at a gate · nothing presented yet');
  });

  it("lists a step run's files from every member session, under the run", async () => {
    // A step-engine run has no session of its own: each member works in its own.
    const stepRun = { ...run('n587', 'running', 'active'), title: 'Live-run probe', session: null };
    setup({
      campaigns: [stepRun],
      publications: vi.fn(async (host: string | null) =>
        host === 'valhalla'
          ? [
              presented(1, 'session-coder', 'notes', '2026-11-08T22:58:00Z'),
              presented(2, 'session-reviewer', 'review', '2026-11-08T23:00:00Z'),
            ]
          : [],
      ),
      session: vi.fn(async (sessionId: string) => ({
        sessionId,
        instanceId: 'valhalla',
        title: `wf-n587-${sessionId.slice(8)}`,
        running: true,
        runSlug: 'n587',
        member: sessionId.slice(8),
      })),
    });

    const group = await screen.findByRole('region', { name: 'No project' });
    await waitFor(() => expect(within(group).getAllByTestId('artifact-row')).toHaveLength(2));
    const [review, notes] = within(group).getAllByTestId('artifact-row');
    expect(review).toHaveAttribute(
      'href',
      '/ting/artifacts/n587?artifact=session-reviewer%3Areview&returnTo=%2Fhere',
    );
    expect(notes).toHaveAttribute(
      'href',
      '/ting/artifacts/n587?artifact=session-coder%3Anotes&returnTo=%2Fhere',
    );
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
