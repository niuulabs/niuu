import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService } from '../../ports';
import type { WorkSummary } from '../../domain/work';
import type { ReviewItem } from '../../domain/review';
import type { Publication } from '../../domain/artifacts';
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
    presented(3, 'session-elsewhere', 'notes', '2026-11-03T12:00:00Z'),
  ],
  ymir: [presented(1, 'session-a2a-tool', 'research-report', '2026-11-03T10:00:00Z')],
};

function setup({
  reviews = [],
  publications = vi.fn(async (host: string | null) => PUBLICATIONS[host ?? ''] ?? []),
  projects = [],
  projectId,
  page = false,
}: {
  reviews?: ReviewItem[];
  publications?: IRunLogService['publications'];
  projects?: Awaited<ReturnType<IRunLogService['projectSessions']>>;
  projectId?: string;
  page?: boolean;
} = {}) {
  const work = {
    list: vi.fn().mockResolvedValue({
      projects: [],
      campaigns: [run('a2a-plan', 'blocked', 'needsAttention'), run('a2a-tool', 'completed', 'completed')],
      executions: [],
      executionNextCursor: null,
      coverage: [],
    }),
    reviews: vi.fn().mockResolvedValue({ items: reviews }),
  } as unknown as IWorkService;
  const runLog = {
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
  return { publications };
}

const rowTexts = () => screen.getAllByTestId('artifact-row').map((row) => row.textContent);

describe('ArtifactList', () => {
  it('lists the documents each run presented, newest first, and what waits on a person', async () => {
    setup({ page: true });

    expect(await screen.findByRole('heading', { name: 'Artifacts' })).toBeInTheDocument();
    const waiting = await screen.findByRole('region', { name: 'Waiting on you' });
    expect(within(waiting).getByText('Developer Planning')).toBeInTheDocument();
    const group = screen.getByRole('region', { name: 'No project' });
    expect(within(group).getAllByTestId('artifact-row').map((row) => row.textContent)).toEqual([
      expect.stringContaining('Research report'),
      expect.stringContaining('plan.md · v2 of 2 versions'),
    ]);
    expect(within(group).getAllByTestId('artifact-row')[1]).toHaveAttribute(
      'href',
      '/ting/artifacts/a2a-plan?artifact=session-a2a-plan%3Aplan',
    );
    expect(screen.queryByText(/notes\.md/)).not.toBeInTheDocument();
  });

  it('filters by status, kind and text', async () => {
    setup();
    await screen.findByRole('region', { name: 'No project' });

    fireEvent.click(screen.getByRole('button', { name: /^Done/ }));
    expect(rowTexts()).toEqual([expect.stringContaining('Research report')]);
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

  it('says which hosts could not be read, and why', async () => {
    setup({
      publications: vi.fn(async (host: string | null) => {
        if (host === 'ymir') throw new Error('Forge unavailable');
        return PUBLICATIONS[host ?? ''] ?? [];
      }),
    });

    expect(await screen.findByText(/The artifacts of 1 Forge host could not be read/)).toBeInTheDocument();
    expect(screen.getByText('ymir: Forge unavailable')).toBeInTheDocument();
    expect(rowTexts()).toEqual([expect.stringContaining('Plan')]);
  });

  it('groups by project, and for one project reads only its hosts', async () => {
    const { publications } = setup({
      projectId: 'p1',
      projects: [{ projectId: 'p1', name: 'Yggdrasil', sessionIds: ['session-a2a-tool'] }],
    });

    await waitFor(() => expect(rowTexts()).toEqual([expect.stringContaining('Research report')]));
    expect(publications).toHaveBeenCalledTimes(1);
    expect(publications).toHaveBeenCalledWith('ymir', expect.anything());
    expect(screen.queryByRole('heading', { name: /Yggdrasil/ })).not.toBeInTheDocument();
  });

  it('names the project a run belongs to', async () => {
    setup({ projects: [{ projectId: 'p1', name: 'Yggdrasil', sessionIds: ['session-a2a-tool'] }] });

    const group = await screen.findByRole('region', { name: 'Yggdrasil' });
    expect(within(group).getByText(/Research report/)).toBeInTheDocument();
  });

  it('says when no run has presented anything yet', async () => {
    setup({ publications: vi.fn().mockResolvedValue([]) });

    expect(await screen.findByText('No run has presented a document yet.')).toBeInTheDocument();
  });
});
