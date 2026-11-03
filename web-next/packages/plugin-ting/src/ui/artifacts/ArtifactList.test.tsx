import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService } from '../../ports';
import type { WorkSummary } from '../../domain/work';
import type { ReviewItem } from '../../domain/review';
import type { RunOutcome } from '../../domain/artifacts';
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
    session: { id: `session-${slug}`, connectionId: 'valhalla' },
    links: { self: '', specialist: '' },
    actions: [],
  };
}

const OUTCOMES: Record<string, RunOutcome[]> = {
  'session-a2a-plan': [
    {
      seq: 1,
      at: '2026-11-02T23:00:00Z',
      eventType: 'delivery.requested',
      participantId: 'skuld-1',
      participantType: 'skuld',
      fields: { prompt: 'Plan NIU-296' },
    },
    {
      seq: 2,
      at: '2026-11-02T23:10:00Z',
      eventType: 'developer.plan.revised',
      participantId: 'flock-developer-analyst',
      participantType: 'ravn',
      fields: { plan: 'Step one\nStep two' },
    },
  ],
  'session-a2a-tool': [
    {
      seq: 1,
      at: '2026-11-03T10:00:00Z',
      eventType: 'review.completed',
      participantId: 'flock-reviewer',
      participantType: 'ravn',
      fields: { comments: '## Major\n- fix it' },
    },
  ],
};

function setup({
  reviews = [],
  outcomes = vi.fn(async (sessionId: string) => OUTCOMES[sessionId] ?? []),
  projects = [],
  projectId,
  page = false,
}: {
  reviews?: ReviewItem[];
  outcomes?: IRunLogService['outcomes'];
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
    outcomes,
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
  return { outcomes };
}

const rowTexts = () => screen.getAllByTestId('artifact-row').map((row) => row.textContent);

describe('ArtifactList', () => {
  it('lists what each run produced, newest first, and what waits on a person', async () => {
    setup({ page: true });

    expect(await screen.findByRole('heading', { name: 'Artifacts' })).toBeInTheDocument();
    const waiting = await screen.findByRole('region', { name: 'Waiting on you' });
    expect(within(waiting).getByText('Plan NIU-296')).toBeInTheDocument();
    const group = screen.getByRole('region', { name: 'No project' });
    expect(within(group).getAllByTestId('artifact-row').map((row) => row.textContent)).toEqual([
      expect.stringContaining('Comments'),
      expect.stringContaining('Plan'),
    ]);
    expect(within(group).getAllByTestId('artifact-row')[1]).toHaveAttribute(
      'href',
      '/ting/artifacts/a2a-plan?artifact=a2a-plan%2Fflock-developer-analyst%2Fplan',
    );
  });

  it('filters by status, kind and text', async () => {
    setup();
    await screen.findByRole('region', { name: 'No project' });

    fireEvent.click(screen.getByRole('button', { name: /^Done/ }));
    expect(rowTexts()).toEqual([expect.stringContaining('Comments')]);
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Plan' } });
    expect(rowTexts().every((text) => text?.includes('Plan'))).toBe(true);
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search artifacts' }), {
      target: { value: 'nothing like this' },
    });
    expect(screen.getByText('Nothing matches these filters.')).toBeInTheDocument();
  });

  it('keeps a gate that waits with nothing to read yet', async () => {
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

  it('says which runs could not be read, and why', async () => {
    setup({
      outcomes: vi.fn(async (sessionId: string) => {
        if (sessionId === 'session-a2a-tool') throw new Error('Resident runtime not found');
        return OUTCOMES[sessionId] ?? [];
      }),
    });

    expect(await screen.findByText(/1 run's log could not be read/)).toBeInTheDocument();
    expect(screen.getByText('Tool build: Resident runtime not found')).toBeInTheDocument();
  });

  it('groups by project, and for one project reads only its runs', async () => {
    const { outcomes } = setup({
      projectId: 'p1',
      projects: [{ projectId: 'p1', name: 'Yggdrasil', sessionIds: ['session-a2a-tool'] }],
    });

    await waitFor(() => expect(rowTexts()).toEqual([expect.stringContaining('Comments')]));
    expect(outcomes).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('heading', { name: /Yggdrasil/ })).not.toBeInTheDocument();
  });

  it('names the project a run belongs to', async () => {
    setup({ projects: [{ projectId: 'p1', name: 'Yggdrasil', sessionIds: ['session-a2a-tool'] }] });

    const group = await screen.findByRole('region', { name: 'Yggdrasil' });
    expect(within(group).getByText(/Comments/)).toBeInTheDocument();
  });

  it('says when no run has produced anything yet', async () => {
    setup({ outcomes: vi.fn().mockResolvedValue([]) });

    expect(await screen.findByText('No run has produced an artifact yet.')).toBeInTheDocument();
  });
});
