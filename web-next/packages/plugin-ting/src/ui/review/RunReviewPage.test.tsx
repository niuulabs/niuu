import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IRunLogService, IWorkService, IWorkflowService } from '../../ports';
import type { ReviewRevision, RunReviews } from '../../domain/review';
import type { WorkSummary } from '../../domain/work';
import type { Publication } from '../../domain/artifacts';
import { RunReviewPage } from './RunReviewPage';

const navigate = vi.hoisted(() => vi.fn());
const search = vi.hoisted(() => ({ value: {} as Record<string, string> }));

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ slug: 'a2a-9' }),
  useSearch: () => search.value,
  useNavigate: () => navigate,
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

function revision(overrides: Partial<ReviewRevision> = {}): ReviewRevision {
  return {
    number: 1,
    gateId: 'gate-1',
    activationId: 'activation-1',
    requestedAt: '2026-10-31T10:00:00Z',
    status: 'pending',
    summary: 'Ready to merge.',
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: {},
    ...overrides,
  };
}

function record(overrides: Partial<RunReviews> = {}): RunReviews {
  return {
    run: {
      slug: 'a2a-9',
      title: 'Plan NIU-1125 only',
      workflowName: 'Infra Change Flow',
      prompt: 'Plan NIU-1125 only\nRead the complete ticket.',
      sessionId: 'session-9',
      repo: 'https://github.com/niuulabs/niuu',
      branch: 'regin',
      startedBy: 'regin',
    },
    reviews: [
      {
        columnId: 'merge-gate',
        title: 'Approve the merge',
        review: null,
        workflowName: 'Infra Change Flow',
        taskId: 'a2a-9',
        sessionId: 'session-9',
        panels: [{ widget: 'summary', title: 'Summary' }],
        revisions: [revision()],
      },
    ],
    unreadableRuns: [],
    ...overrides,
  };
}

function campaign(status: string, condition: WorkSummary['condition']): WorkSummary {
  return {
    id: 'campaign:9',
    kind: 'workflowLaunch',
    title: 'Infra Change Flow',
    campaignSlug: 'a2a-9',
    condition,
    rawState: { source: 'campaign', status },
    currentStep: null,
    attention: null,
    createdAt: '2026-10-31T09:00:00Z',
    updatedAt: '2026-10-31T10:00:00Z',
    source: null,
    workflow: { id: 'wf-infra', version: '1.2.0', documentRevision: null },
    session: { id: 'session-9', connectionId: 'valhalla' },
    links: { self: '', specialist: '' },
    actions: [],
  };
}

function presented(seq: number, overrides: Partial<Publication> = {}): Publication {
  return {
    seq,
    sessionId: 'session-9',
    providerKey: 'session-9:plan',
    revisionId: `pf_${seq}`,
    title: 'plan.md',
    url: `/api/v1/forge/sessions/session-9/files/presented/pf_${seq}`,
    at: `2026-10-31T09:3${seq}:00Z`,
    mime: 'text/markdown',
    versionLabel: `v${seq}`,
    ...overrides,
  };
}

const PLAN: Publication[] = [presented(1)];
const TEXTS: Record<string, string> = { pf_1: 'Step one\nStep two', pf_2: 'Step one, better' };
const presentedText = vi.fn(async (_session: string, _host: string | null, fileId: string) => {
  const text = TEXTS[fileId];
  if (text === undefined) throw new Error(`No file ${fileId}`);
  return text;
});

function setup({
  runReviews = vi.fn().mockResolvedValue(record()),
  decideRunGate = vi.fn(),
  status = ['blocked', 'needsAttention'] as [string, WorkSummary['condition']],
  publications = vi.fn().mockResolvedValue([]),
  sendMessage = vi.fn().mockResolvedValue(undefined),
  launchWorkflow = vi.fn(),
}: {
  runReviews?: IWorkService['runReviews'];
  decideRunGate?: IWorkService['decideRunGate'];
  status?: [string, WorkSummary['condition']];
  publications?: IRunLogService['publications'];
  sendMessage?: IRunLogService['sendMessage'];
  launchWorkflow?: IWorkflowService['launchWorkflow'];
} = {}) {
  const work = {
    runReviews,
    decideRunGate,
    reviews: vi.fn().mockResolvedValue({ items: [] }),
    list: vi.fn().mockResolvedValue({
      projects: [],
      campaigns: [campaign(...status)],
      executions: [],
      executionNextCursor: null,
      coverage: [],
    }),
  } as unknown as IWorkService;
  const runLog = {
    publications,
    presentedText,
    projectSessions: vi.fn().mockResolvedValue([]),
    sendMessage,
  } as unknown as IRunLogService;
  const workflows = { launchWorkflow } as unknown as IWorkflowService;
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider
        services={{ 'ting.work': work, 'ting.runLog': runLog, 'ting.workflows': workflows }}
      >
        <RunReviewPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { decideRunGate, sendMessage, launchWorkflow, publications };
}

describe('RunReviewPage', () => {
  beforeEach(() => {
    navigate.mockReset();
    search.value = {};
  });

  it('introduces the run and what started it', async () => {
    setup();

    expect(await screen.findByRole('heading', { name: 'Infra Change Flow' })).toBeInTheDocument();
    expect(screen.getByText('Started by regin')).toBeInTheDocument();
    expect(screen.getByText('On regin')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open the session' })).toHaveAttribute(
      'href',
      '/volundr/sessions/session-9',
    );
    expect(screen.getByText('Ready to merge.')).toBeInTheDocument();
  });

  it('answers the pending gate with what the person saw', async () => {
    const { decideRunGate } = setup({ decideRunGate: vi.fn().mockResolvedValue(undefined) });

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(decideRunGate).toHaveBeenCalledWith('a2a-9', {
        gateId: 'gate-1',
        activationId: 'activation-1',
        decision: 'approve',
        notes: '',
      }),
    );
  });

  it('asks for what should change, and says where it goes', async () => {
    const { decideRunGate } = setup({ decideRunGate: vi.fn().mockResolvedValue(undefined) });

    expect(await screen.findByText(/Changes go back to the agent/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Pin the version.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));

    await waitFor(() =>
      expect(decideRunGate).toHaveBeenCalledWith(
        'a2a-9',
        expect.objectContaining({ decision: 'request_changes', notes: 'Pin the version.' }),
      ),
    );
  });

  it('says why a decision did not go through', async () => {
    setup({ decideRunGate: vi.fn().mockRejectedValue(new Error('Review it again.')) });

    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));

    expect(await screen.findByText('Review it again.')).toBeInTheDocument();
  });

  it('offers no decision once the gate is answered', async () => {
    const decided = record();
    decided.reviews[0]!.revisions = [revision({ status: 'approved', decidedBy: 'jozef' })];
    setup({ runReviews: vi.fn().mockResolvedValue(decided), status: ['completed', 'completed'] });

    expect(await screen.findByText('by jozef')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
  });

  it('says when the session is gone and nothing was produced', async () => {
    setup({
      runReviews: vi
        .fn()
        .mockResolvedValue(record({ reviews: [], unreadableRuns: ['Infra Change Flow'] })),
    });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The session of Infra Change Flow is gone',
    );
    expect(screen.getByRole('link', { name: 'Back to Artifacts' })).toHaveAttribute(
      'href',
      '/ting/artifacts',
    );
  });

  it('says when the run cannot be read', async () => {
    setup({ runReviews: vi.fn().mockRejectedValue(new Error('Run not found: a2a-9')) });

    expect(await screen.findByRole('alert')).toHaveTextContent('Run not found: a2a-9');
  });

  it('says when what the run presented cannot be read', async () => {
    setup({ publications: vi.fn().mockRejectedValue(new Error('Forge unavailable')) });

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'What the run presented could not be read: Forge unavailable',
    );
  });

  it("reads one session's presented files and shows each version's text", async () => {
    search.value = { artifact: 'session-9:plan' };
    const { publications } = setup({
      runReviews: vi.fn().mockResolvedValue(record({ reviews: [] })),
      status: ['running', 'active'],
      publications: vi.fn().mockResolvedValue([...PLAN, presented(2)]),
    });

    expect(await screen.findByText(/Step one, better/)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'r2' })).toBeInTheDocument();
    expect(publications).toHaveBeenCalledWith(
      'valhalla',
      expect.objectContaining({ sessionId: 'session-9' }),
    );
    expect(screen.getByText(/Read the complete ticket/)).toBeInTheDocument();
  });

  it('says which version could not be read', async () => {
    setup({
      runReviews: vi.fn().mockResolvedValue(record({ reviews: [] })),
      status: ['running', 'active'],
      publications: vi.fn().mockResolvedValue([presented(7)]),
    });

    expect(
      await screen.findByText(/plan\.md \(v7\) could not be read: No file pf_7/),
    ).toBeInTheDocument();
  });

  it('sends notes on a running artifact to the run', async () => {
    const { sendMessage } = setup({
      runReviews: vi.fn().mockResolvedValue(record({ reviews: [] })),
      status: ['running', 'active'],
      publications: vi.fn().mockResolvedValue(PLAN),
    });

    expect(
      await screen.findByRole('heading', { name: 'Send back to the run' }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Name the setting.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to the run' }));

    await waitFor(() =>
      expect(sendMessage).toHaveBeenCalledWith(
        'session-9',
        'valhalla',
        'Notes on the plan (v1, artifact key plan):\n\nName the setting.',
      ),
    );
  });

  it('starts a revision of a finished artifact and opens the new run', async () => {
    const launchWorkflow = vi.fn().mockResolvedValue({ slug: 'a2a-10' });
    setup({
      runReviews: vi.fn().mockResolvedValue(record({ reviews: [] })),
      status: ['completed', 'completed'],
      publications: vi.fn().mockResolvedValue(PLAN),
      launchWorkflow,
    });

    expect(await screen.findByText(/Step two/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Name the setting.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start a revision' }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/ting/artifacts/a2a-10' }));
    const [workflowId, request] = launchWorkflow.mock.calls[0]!;
    expect(workflowId).toBe('wf-infra');
    expect(request).toMatchObject({
      repo: 'https://github.com/niuulabs/niuu',
      branch: 'regin',
      connectionId: 'valhalla',
      workflowVersion: '1.2.0',
    });
    expect(request.prompt).toContain('Plan NIU-1125 only');
    expect(request.prompt).toContain("Reviewer's notes:\nName the setting.");
    expect(request.prompt).toContain('Previous plan:\nStep one\nStep two');
  });

  it('answers a waiting gate from the artifact version it shows', async () => {
    const waiting = record();
    waiting.reviews[0]!.revisions = [
      revision({
        artifacts: [
          {
            artifactKey: 'plan',
            providerKey: 'session-9:plan',
            revisionId: 'pf_1',
            name: 'plan.md',
            versionLabel: 'v1',
            mime: 'text/markdown',
            url: null,
          },
        ],
      }),
    ];
    const { decideRunGate } = setup({
      runReviews: vi.fn().mockResolvedValue(waiting),
      publications: vi.fn().mockResolvedValue(PLAN),
      decideRunGate: vi.fn().mockResolvedValue(undefined),
    });
    search.value = { artifact: 'session-9:plan' };

    fireEvent.click(await screen.findByRole('tab', { name: 'Plan' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(decideRunGate).toHaveBeenCalledWith(
        'a2a-9',
        expect.objectContaining({ gateId: 'gate-1', decision: 'approve' }),
      ),
    );
  });

  it("reads a gate's plan panel from the version it showed, not the outcome copy", async () => {
    const gate = record();
    gate.reviews[0]!.panels = [
      { widget: 'document', title: 'Plan', source: { kind: 'outcome', value: 'plan' } },
    ];
    gate.reviews[0]!.revisions = [
      revision({
        fields: { plan: 'copied outcome' },
        artifacts: [
          {
            artifactKey: 'plan',
            providerKey: 'session-9:plan',
            revisionId: 'pf_2',
            name: 'plan.md',
            versionLabel: 'v2',
            mime: 'text/markdown',
            url: null,
          },
        ],
      }),
    ];
    setup({
      runReviews: vi.fn().mockResolvedValue(gate),
      publications: vi.fn().mockResolvedValue([...PLAN, presented(2)]),
    });

    expect(await screen.findByText(/Step one, better/)).toBeInTheDocument();
    expect(screen.queryByText('copied outcome')).not.toBeInTheDocument();
    // The plan is read in the gate's own panel, so it has no second tab.
    expect(screen.queryByRole('tab', { name: 'Plan' })).not.toBeInTheDocument();
  });

  it('says so when Work does not list the run', async () => {
    const work = {
      runReviews: vi.fn().mockResolvedValue(record()),
      reviews: vi.fn().mockResolvedValue({ items: [] }),
      list: vi.fn().mockResolvedValue({
        projects: [],
        campaigns: [],
        executions: [],
        executionNextCursor: null,
        coverage: [],
      }),
    } as unknown as IWorkService;
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ServicesProvider services={{ 'ting.work': work, 'ting.runLog': {} }}>
          <RunReviewPage />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Run a2a-9 is not among the runs Work lists',
    );
  });
});
