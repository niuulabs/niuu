import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { IFileSystemPort } from '@niuulabs/ui';
import type { IWorkService } from '../../ports';
import type { ReviewRevision, RunReview, TicketReviews } from '../../domain/review';
import type { WorkDetail, WorkTask } from '../../domain/work';
import { TicketReviewPage } from './TicketReviewPage';
import { ReviewPanelSlot } from './reviewPanels';

const sendMessage = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useParams: () => ({ projectId: 'project-1', issueId: 'issue-1' }),
  Link: ({ children }: { children: React.ReactNode }) => <a href="/board">{children}</a>,
}));

vi.mock('@niuulabs/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@niuulabs/ui')>()),
  useSkuldChat: () => ({ messages: [], sendMessage }),
  SessionChat: () => <div data-testid="session-chat" />,
}));

function revision(overrides: Partial<ReviewRevision> = {}): ReviewRevision {
  return {
    number: 1,
    gateId: 'gate-1',
    activationId: 'activation-1',
    requestedAt: '2026-10-31T10:00:00Z',
    status: 'pending',
    summary: 'Agent review passed.',
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: { plan: '## Approach\nTrace every tick' },
    ...overrides,
  };
}

function planReview(revisions: ReviewRevision[] = [revision()]): RunReview {
  return {
    columnId: 'plan-review',
    title: 'Plan review',
    review: 'plan',
    workflowName: 'Ticket Planning Flow',
    taskId: 'a2a-1',
    sessionId: 'session-1',
    panels: [{ widget: 'document', title: 'Plan', source: { kind: 'outcome', value: 'plan' } }],
    revisions,
  };
}

function task(overrides: Partial<WorkTask> = {}): WorkTask {
  return {
    source: {
      connectionId: 'linear',
      provider: 'linear',
      projectId: 'project-1',
      url: 'https://linear.example/issue-1',
      status: 'In Review',
      statusType: 'started',
      issueId: 'issue-1',
      identifier: 'PLT-212',
    },
    title: 'Trace queue wait time',
    description: 'We cannot see queue wait.',
    operationalRun: null,
    dispatch: null,
    actions: [],
    coordinator: {
      sessionId: 'coordinator-1',
      status: 'active',
      connectionId: null,
      chatEndpoint: 'ws://forge/s/coordinator',
    },
    board: {
      columnId: 'plan-review',
      state: 'needsDecision',
      gate: {
        id: 'gate-1',
        activationId: 'activation-1',
        nodeId: 'plan-review-gate',
        label: 'Plan review',
        condition: '',
        instructions: '',
        summary: '',
        requestedAt: '',
        attempt: 1,
      },
    },
    ...overrides,
  };
}

function detail(work: WorkTask): WorkDetail {
  return {
    item: {} as WorkDetail['item'],
    tasks: [work],
    coverage: [],
    board: {
      columns: [
        {
          id: 'planning',
          title: 'Planning',
          kind: 'work',
          review: null,
          approveColumnId: null,
          changesColumnId: null,
          workflowId: 'w1',
        },
        {
          id: 'plan-review',
          title: 'Plan review',
          kind: 'gate',
          review: 'plan',
          approveColumnId: 'plan-record',
          changesColumnId: 'planning',
          workflowId: 'w1',
        },
        {
          id: 'plan-record',
          title: 'Recording plan',
          kind: 'work',
          review: null,
          approveColumnId: null,
          changesColumnId: null,
          workflowId: 'w1',
        },
      ],
    },
  };
}

function setup(reviews: TicketReviews, work: WorkTask = task()) {
  const service = {
    get: vi.fn().mockResolvedValue(detail(work)),
    ticketReviews: vi.fn().mockResolvedValue(reviews),
    decideGate: vi.fn().mockResolvedValue(undefined),
  };
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ServicesProvider services={{ 'ting.work': service as unknown as IWorkService }}>
        <TicketReviewPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return service;
}

function select(text: string) {
  vi.spyOn(window, 'getSelection').mockReturnValue({
    toString: () => text,
  } as unknown as Selection);
  fireEvent.mouseUp(screen.getByRole('region', { name: 'Plan' }));
}

describe('TicketReviewPage', () => {
  beforeEach(() => {
    sendMessage.mockClear();
    vi.restoreAllMocks();
  });

  it('shows the plan beside its ticket and the coordinator', async () => {
    setup({ reviews: [planReview()], unreadableRuns: [] });

    expect(await screen.findByText('Trace every tick')).toBeInTheDocument();
    expect(screen.getByText('We cannot see queue wait.')).toBeInTheDocument();
    expect(screen.getByTestId('session-chat')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve → Recording plan' })).toBeEnabled();
  });

  it('asks for a note before sending changes back', async () => {
    const service = setup({ reviews: [planReview()], unreadableRuns: [] });

    fireEvent.click(await screen.findByRole('button', { name: 'Request changes → Planning' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Say what should change first.');
    expect(service.decideGate).not.toHaveBeenCalled();
  });

  it('quotes selected text into the note and sends it as the gate comment', async () => {
    const service = setup({ reviews: [planReview()], unreadableRuns: [] });
    await screen.findByText('Trace every tick');

    select('Trace every tick');
    fireEvent.click(screen.getByRole('button', { name: 'Add to my note' }));
    const note = screen.getByLabelText('Your note');
    expect(note).toHaveValue('> Trace every tick\n');

    fireEvent.change(note, { target: { value: '> Trace every tick\nSample instead.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Request changes → Planning' }));

    await waitFor(() =>
      expect(service.decideGate).toHaveBeenCalledWith('project-1', 'issue-1', {
        gateId: 'gate-1',
        activationId: 'activation-1',
        decision: 'request_changes',
        notes: '> Trace every tick\nSample instead.',
      }),
    );
  });

  it('asks the coordinator about selected text in its own chat', async () => {
    setup({ reviews: [planReview()], unreadableRuns: [] });
    await screen.findByText('Trace every tick');

    select('Trace every tick');
    fireEvent.click(screen.getByRole('button', { name: 'Ask the coordinator' }));
    fireEvent.change(screen.getByLabelText('Ask about this'), {
      target: { value: 'Why not sample?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    expect(sendMessage).toHaveBeenCalledWith('> Trace every tick\n\nWhy not sample?', []);
    expect(screen.queryByLabelText('Ask about this')).not.toBeInTheDocument();
  });

  it('shows what changed between revisions and who asked for it', async () => {
    setup({
      reviews: [
        planReview([
          revision({
            gateId: 'gate-0',
            status: 'changes_requested',
            decidedBy: 'jo',
            notes: 'Sample instead.',
            fields: { plan: 'Trace every tick' },
          }),
          revision({ number: 2, gateId: 'gate-1', fields: { plan: 'Sample ticks' } }),
        ]),
      ],
      unreadableRuns: [],
    });

    fireEvent.click(await screen.findByRole('button', { name: 'Show changes since r1' }));

    const diff = screen.getByLabelText('Changes to Plan');
    expect(within(diff).getByText('Trace every tick')).toHaveClass('ting-review-diff__removed');
    expect(within(diff).getByText('Sample ticks')).toHaveClass('ting-review-diff__added');
    expect(screen.getByText('Sample instead.')).toBeInTheDocument();
  });

  it('holds decisions while an older revision is on screen', async () => {
    setup({
      reviews: [
        planReview([
          revision({ gateId: 'gate-0', status: 'changes_requested' }),
          revision({ number: 2 }),
        ]),
      ],
      unreadableRuns: [],
    });

    fireEvent.click(await screen.findByRole('tab', { name: 'r1' }));

    expect(screen.getByRole('button', { name: 'Approve → Recording plan' })).toBeDisabled();
  });

  it('offers only what can be done with selected text', async () => {
    setup(
      { reviews: [planReview([revision({ status: 'approved' })])], unreadableRuns: [] },
      task({
        board: { columnId: 'plan-record', state: 'running', gate: null },
        coordinator: null,
      }),
    );
    await screen.findByText('Trace every tick');

    select('Trace every tick');

    expect(screen.queryByRole('group', { name: 'Selected text' })).not.toBeInTheDocument();
  });

  it('keeps asking open after a decision, while the coordinator is there', async () => {
    setup(
      { reviews: [planReview([revision({ status: 'approved' })])], unreadableRuns: [] },
      task({ board: { columnId: 'plan-record', state: 'running', gate: null } }),
    );
    await screen.findByText('Trace every tick');

    select('Trace every tick');

    expect(screen.getByRole('button', { name: 'Ask the coordinator' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add to my note' })).not.toBeInTheDocument();
  });

  it('offers no decision once a review is decided', async () => {
    setup(
      { reviews: [planReview([revision({ status: 'approved' })])], unreadableRuns: [] },
      task({ board: { columnId: 'plan-record', state: 'running', gate: null } }),
    );

    expect(await screen.findAllByText('Approved')).not.toHaveLength(0);
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Your note')).not.toBeInTheDocument();
  });

  it('switches between the reviews of a ticket', async () => {
    const code: RunReview = {
      ...planReview([revision({ gateId: 'gate-2', status: 'approved', summary: 'Merged.' })]),
      columnId: 'code-review',
      title: 'Code review',
      panels: [{ widget: 'summary', title: 'Result' }],
    };
    setup({ reviews: [planReview(), code], unreadableRuns: [] });

    fireEvent.click(await screen.findByRole('tab', { name: 'Code review' }));

    expect(screen.getByText('Merged.')).toBeInTheDocument();
  });

  it('moves between the panels a workflow declares', async () => {
    const code: RunReview = {
      ...planReview([revision()]),
      panels: [
        { widget: 'summary', title: 'Result' },
        { widget: 'pullRequest', title: 'Pull request' },
      ],
    };
    setup({ reviews: [code], unreadableRuns: [] });

    fireEvent.click(await screen.findByRole('tab', { name: 'Pull request' }));

    expect(screen.getByText('This run has no pull request yet.')).toBeInTheDocument();
  });

  it('marks what was quoted and links the project plan', async () => {
    setup({ reviews: [planReview()], unreadableRuns: [], planSlug: 'plan-1' });
    await screen.findByText('Trace every tick');

    select('Trace every tick');
    fireEvent.click(screen.getByRole('button', { name: 'Add to my note' }));

    expect(screen.getByRole('region', { name: 'Plan' }).querySelector('mark')).toHaveTextContent(
      'Trace every tick',
    );
    expect(screen.getByRole('link', { name: 'Open the project plan' })).toBeInTheDocument();
  });

  it('says when a run can no longer be read', async () => {
    setup({ reviews: [], unreadableRuns: ['Ticket Planning Flow'] });

    expect(await screen.findByRole('alert')).toHaveTextContent('Ticket Planning Flow');
    expect(screen.getByText(/Nothing to review on PLT-212/)).toBeInTheDocument();
  });

  it('says when the review cannot be read', async () => {
    const service = {
      get: vi.fn().mockResolvedValue(detail(task())),
      ticketReviews: vi.fn().mockRejectedValue(new Error('Forge is down')),
    };
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <ServicesProvider services={{ 'ting.work': service as unknown as IWorkService }}>
          <TicketReviewPage />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Forge is down');
  });
});

describe('ReviewPanelSlot', () => {
  it('says when a stopped session cannot show its changes', () => {
    render(
      <ReviewPanelSlot
        panel={{ widget: 'diff', title: 'Changes' }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
        chatEndpoint={null}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('session of this run is stopped');
  });

  it('previews a file the workflow declares from the run workspace', async () => {
    const downloadFile = vi.fn().mockResolvedValue(new Blob(['# Quality'], { type: 'text/plain' }));
    render(
      <ReviewPanelSlot
        panel={{
          widget: 'file',
          title: 'Report',
          source: { kind: 'file', value: 'report/quality.md' },
        }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
        sessionId="run-1"
        filesystem={{ downloadFile } as unknown as IFileSystemPort}
      />,
    );

    await waitFor(() =>
      expect(downloadFile.mock.calls[0]?.slice(0, 2)).toEqual([
        'run-1',
        '/workspace/report/quality.md',
      ]),
    );
  });

  it('says when a file panel has no file browser', () => {
    render(
      <ReviewPanelSlot
        panel={{ widget: 'file', title: 'Report', source: { kind: 'file', value: 'a.pdf' } }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('no file browser');
  });

  it('frames a declared link', () => {
    render(
      <ReviewPanelSlot
        panel={{
          widget: 'link',
          title: 'Camera',
          source: { kind: 'url', value: 'https://printer.example/cam' },
        }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
      />,
    );

    expect(screen.getByTitle('Preview of Camera')).toHaveAttribute(
      'src',
      'https://printer.example/cam',
    );
  });

  it('says when no file browser is connected', () => {
    render(
      <ReviewPanelSlot
        panel={{ widget: 'files', title: 'Files' }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('no file browser');
  });

  it('mounts the shared diff for a running session', () => {
    render(
      <ReviewPanelSlot
        panel={{ widget: 'diff', title: 'Changes' }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
        chatEndpoint="ws://forge/s/run/session"
      />,
    );

    expect(screen.getByTestId('diffs-tab')).toBeInTheDocument();
  });

  it('names a panel this app does not have', () => {
    render(
      <ReviewPanelSlot
        panel={{ widget: 'model', title: 'Armchair' }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl={null}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('needs a model panel');
  });

  it('says when the log holds no document', () => {
    render(
      <ReviewPanelSlot
        panel={{
          widget: 'document',
          title: 'Plan',
          source: { kind: 'outcome', value: 'plan' },
        }}
        revision={revision({ fields: {} })}
        compareWith={null}
        pullRequestUrl={null}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('holds no plan');
  });

  it('links the pull request, and says when there is none', () => {
    const { rerender } = render(
      <ReviewPanelSlot
        panel={{ widget: 'pullRequest', title: 'Pull request' }}
        revision={revision()}
        compareWith={null}
        pullRequestUrl="https://github.example/pr/1"
      />,
    );
    expect(screen.getByRole('link', { name: /Open pull request/ })).toHaveAttribute(
      'href',
      'https://github.example/pr/1',
    );

    rerender(
      <ReviewPanelSlot
        panel={{ widget: 'pullRequest', title: 'Pull request' }}
        revision={revision({ summary: '' })}
        compareWith={null}
        pullRequestUrl={null}
      />,
    );
    expect(screen.getByText('This run has no pull request yet.')).toBeInTheDocument();
  });
});
