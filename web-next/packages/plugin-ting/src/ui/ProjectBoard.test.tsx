import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { ApiClientError } from '@niuulabs/query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createMockWorkService } from '../adapters/mock';
import type { WorkDetail, WorkTask } from '../domain/work';
import type { IWorkService } from '../ports';
import { ProjectBoard } from './ProjectBoard';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

const GATED = 'demo-issue-207';

function renderBoard(detail: WorkDetail, service: IWorkService) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ServicesProvider services={{ 'ting.work': service }}>
        <ProjectBoard board={detail.board!} projectId="demo-platform" tasks={detail.tasks} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

function drag(issueId: string, columnId: string) {
  const dataTransfer = {
    data: {} as Record<string, string>,
    setData(type: string, value: string) {
      this.data[type] = value;
    },
    getData(type: string) {
      return this.data[type] ?? '';
    },
  };
  fireEvent.dragStart(screen.getByTestId(`board-ticket-${issueId}`), { dataTransfer });
  fireEvent.dragOver(screen.getByTestId(`board-column-${columnId}`), { dataTransfer });
  fireEvent.drop(screen.getByTestId(`board-column-${columnId}`), { dataTransfer });
}

function startButton(issueId: string) {
  return within(screen.getByTestId(`board-ticket-${issueId}`)).getByRole('button', {
    name: 'Start',
  });
}

function withTask(detail: WorkDetail, issueId: string, change: Partial<WorkTask>): WorkDetail {
  return {
    ...detail,
    tasks: detail.tasks.map((task) =>
      task.source.issueId === issueId ? { ...task, ...change } : task,
    ),
  };
}

function openGate() {
  fireEvent.click(screen.getByRole('button', { name: 'Implement retry budget' }));
}

describe('ProjectBoard', () => {
  let service: IWorkService;
  let detail: WorkDetail;

  beforeEach(async () => {
    service = createMockWorkService();
    detail = await service.get('project', 'demo-platform');
  });

  it('shows each workflow’s columns as one run between the tracker’s own', () => {
    renderBoard(detail, service);

    expect(screen.getByRole('button', { name: /Ticket Planning Flow/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reviewed Delivery Flow/ })).toBeInTheDocument();
    expect(screen.getAllByText('One run')).toHaveLength(2);
    const review = screen.getByTestId('board-column-code-review');
    expect(within(review).getByText('Implement retry budget')).toBeInTheDocument();
    expect(within(review).getByText('Needs you')).toBeInTheDocument();
    expect(screen.getByTestId('board-column-backlog')).toBeInTheDocument();
    expect(screen.getByTestId('board-column-done')).toBeInTheDocument();
  });

  it('opens the decision with what each answer does', () => {
    renderBoard(detail, service);

    openGate();

    const panel = screen.getByRole('complementary');
    expect(within(panel).getByText('Is this ready to merge?')).toBeInTheDocument();
    expect(within(panel).getByText(/Approve continues to Merging\./)).toBeInTheDocument();
    expect(within(panel).getByText(/Changes go back to Building/)).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: /Open pull request/ })).toHaveAttribute(
      'href',
      'https://github.com/niuulabs/niuu/pull/1042',
    );
  });

  it('approves the gate the reviewer saw', async () => {
    const decideGate = vi.spyOn(service, 'decideGate');
    renderBoard(detail, service);
    openGate();

    fireEvent.click(screen.getByRole('button', { name: 'Approve → Merging' }));

    await waitFor(() =>
      expect(decideGate).toHaveBeenCalledWith('demo-platform', GATED, {
        gateId: 'demo-gate-207',
        activationId: 'demo-activation-207',
        decision: 'approve',
        notes: '',
      }),
    );
  });

  it('needs a comment before requesting changes', async () => {
    const decideGate = vi.spyOn(service, 'decideGate');
    renderBoard(detail, service);
    openGate();

    fireEvent.click(screen.getByRole('button', { name: 'Request changes → Building' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Say what should change first.');
    expect(decideGate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'Add a test.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Request changes → Building' }));

    await waitFor(() =>
      expect(decideGate).toHaveBeenCalledWith(
        'demo-platform',
        GATED,
        expect.objectContaining({ decision: 'request_changes', notes: 'Add a test.' }),
      ),
    );
  });

  it('shows a refused decision', async () => {
    vi.spyOn(service, 'decideGate').mockRejectedValue(new Error('Forbidden'));
    renderBoard(detail, service);
    openGate();

    fireEvent.click(screen.getByRole('button', { name: 'Approve → Merging' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Forbidden');
  });

  it('shows why the server refused a decision', async () => {
    vi.spyOn(service, 'decideGate').mockRejectedValue(
      new ApiClientError('API request failed: 409', 409, 'This gate changed. Review it again.'),
    );
    renderBoard(detail, service);
    openGate();

    fireEvent.click(screen.getByRole('button', { name: 'Approve → Merging' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Review it again.');
  });

  it('never approves on a drag alone', () => {
    const decideGate = vi.spyOn(service, 'decideGate');
    renderBoard(detail, service);

    drag(GATED, 'merging');

    expect(screen.getByText(/A drag never approves on its own/)).toBeInTheDocument();
    expect(decideGate).not.toHaveBeenCalled();
  });

  it('turns a drag back into a request for a comment', () => {
    renderBoard(detail, service);

    drag(GATED, 'building');

    expect(screen.getByLabelText('Comment')).toHaveFocus();
  });

  it('starts a ready ticket dropped on the first column through its coordinator', async () => {
    const startTicket = vi.spyOn(service, 'startTicket');
    renderBoard(detail, service);

    drag('demo-issue-209', 'planning');

    await waitFor(() =>
      expect(startTicket).toHaveBeenCalledWith('demo-platform', 'demo-issue-209', 'planning'),
    );
    expect(screen.getByRole('complementary', { name: 'PLT-209' })).toBeInTheDocument();
  });

  it('starts a ready ticket from its card', async () => {
    const startTicket = vi.spyOn(service, 'startTicket');
    renderBoard(detail, service);

    fireEvent.click(startButton('demo-issue-209'));

    await waitFor(() => expect(startTicket).toHaveBeenCalled());
  });

  it('offers a retry on a ticket left starting', async () => {
    const startTicket = vi.spyOn(service, 'startTicket');
    renderBoard(
      withTask(detail, GATED, { board: { columnId: 'building', state: 'starting' } }),
      service,
    );

    fireEvent.click(
      within(screen.getByTestId(`board-ticket-${GATED}`)).getByRole('button', { name: 'Retry' }),
    );

    await waitFor(() => expect(startTicket).toHaveBeenCalled());
  });

  it('shows why a start failed and offers a retry', () => {
    renderBoard(
      withTask(detail, GATED, {
        board: { columnId: 'building', state: 'blocked', reason: 'The session would not start.' },
        coordinator: {
          sessionId: 'session-1',
          status: 'active',
          connectionId: null,
          chatEndpoint: null,
          startError: 'The session would not start.',
        },
      }),
      service,
    );

    const card = within(screen.getByTestId(`board-ticket-${GATED}`));
    expect(card.getByText('The session would not start.')).toBeInTheDocument();
    expect(card.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('shows who made the last decision and links the pull request', () => {
    const gated = detail.tasks.find((task) => task.source.issueId === GATED)!;
    renderBoard(
      withTask(detail, GATED, {
        board: {
          ...gated.board!,
          lastDecision: {
            label: 'Code review',
            status: 'changes_requested',
            decidedBy: 'jozef@niuu.world',
            notes: 'Rename it.',
            decidedAt: '2026-10-30T10:00:00Z',
          },
        },
        workflowRun: {
          taskId: 'a2a-1',
          workflowName: 'Reviewed Delivery Flow',
          status: 'blocked',
          sessionId: 'session-1',
          prUrl: 'https://github.com/o/r/pull/4',
        },
      }),
      service,
    );
    openGate();

    expect(screen.getByText('Code review: Changes requested')).toBeInTheDocument();
    expect(screen.getByText('jozef@niuu.world')).toBeInTheDocument();
    expect(screen.getByText('Rename it.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open pull request/ })).toHaveAttribute(
      'href',
      'https://github.com/o/r/pull/4',
    );
  });

  it('keeps the last decision readable after the gate has passed', () => {
    renderBoard(
      withTask(detail, GATED, {
        board: {
          columnId: 'building',
          state: 'running',
          lastDecision: {
            label: 'Code review',
            status: 'approved',
            decidedBy: 'jozef@niuu.world',
            notes: '',
            decidedAt: '2026-10-30T10:00:00Z',
          },
        },
      }),
      service,
    );
    openGate();

    fireEvent.click(screen.getByRole('tab', { name: 'Review' }));

    expect(screen.getByText('Code review: Approved')).toBeInTheDocument();
  });

  it('starts the next workflow of a ticket that finished the last one', async () => {
    const startTicket = vi.spyOn(service, 'startTicket');
    renderBoard(detail, service);
    const card = screen.getByTestId('board-ticket-demo-issue-212');
    expect(within(card).getByText('Ready to start')).toBeInTheDocument();

    fireEvent.click(startButton('demo-issue-212'));

    await waitFor(() =>
      expect(startTicket).toHaveBeenCalledWith('demo-platform', 'demo-issue-212', undefined),
    );
  });

  it('asks before a drop skips a workflow, and starts when told to', async () => {
    const startTicket = vi.spyOn(service, 'startTicket');
    renderBoard(detail, service);

    drag('demo-issue-209', 'building');

    expect(screen.getByRole('alert')).toHaveTextContent(
      'This skips Ticket Planning Flow. PLT-209 starts here without running it.',
    );
    expect(startTicket).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Skip and start' }));

    await waitFor(() =>
      expect(startTicket).toHaveBeenCalledWith('demo-platform', 'demo-issue-209', 'building'),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('leaves a ticket where it is when the skip is declined', () => {
    const startTicket = vi.spyOn(service, 'startTicket');
    renderBoard(detail, service);

    drag('demo-issue-209', 'building');
    fireEvent.click(screen.getByRole('button', { name: 'Keep it here' }));

    expect(screen.queryByRole('alert')).toBeNull();
    expect(startTicket).not.toHaveBeenCalled();
  });

  it('shows why a ticket could not start', async () => {
    vi.spyOn(service, 'startTicket').mockRejectedValue(
      new ApiClientError('API request failed: 502', 502, 'The coordinator could not be started'),
    );
    renderBoard(detail, service);

    fireEvent.click(startButton('demo-issue-209'));

    expect(await screen.findByRole('status')).toHaveTextContent(
      'The coordinator could not be started',
    );
  });

  it('shows a coordinator on the card and in the panel', () => {
    const coordinated: WorkTask = {
      ...detail.tasks[2]!,
      coordinator: {
        sessionId: 'coordinator-1',
        status: 'active',
        connectionId: null,
        chatEndpoint: null,
      },
    };
    renderBoard({ ...detail, tasks: [coordinated] }, service);

    expect(screen.getByText('Coordinator active')).toBeInTheDocument();
    openGate();
    fireEvent.click(screen.getByRole('tab', { name: 'Coordinator' }));

    expect(screen.getByText('The coordinator is starting up.')).toBeInTheDocument();
  });

  it('says when a ticket has no coordinator yet', () => {
    renderBoard(detail, service);

    fireEvent.click(screen.getByRole('button', { name: 'Trace queue wait time' }));

    expect(screen.getByRole('tab', { name: 'Coordinator' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByText(/No coordinator yet/)).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Review' })).not.toBeInTheDocument();
  });

  it('says when a coordinator has closed with its ticket', () => {
    const closed: WorkTask = {
      ...detail.tasks[4]!,
      coordinator: {
        sessionId: 'coordinator-1',
        status: 'closed',
        connectionId: null,
        chatEndpoint: null,
      },
    };
    renderBoard({ ...detail, tasks: [closed] }, service);

    fireEvent.click(screen.getByRole('button', { name: 'Trace queue wait time' }));

    expect(screen.getByText(/so is its coordinator/)).toBeInTheDocument();
  });

  it('shows the run and its links on the Workflow tab', () => {
    const running: WorkTask = {
      ...detail.tasks[2]!,
      workflowRun: {
        taskId: 'a2a-run',
        workflowName: 'Reviewed Delivery Flow',
        status: 'running',
        sessionId: 'run-1',
      },
      coordinator: {
        sessionId: 'coordinator-1',
        status: 'active',
        connectionId: null,
        chatEndpoint: null,
      },
    };
    renderBoard({ ...detail, tasks: [running] }, service);
    openGate();

    fireEvent.click(screen.getByRole('tab', { name: 'Workflow' }));

    expect(screen.getByText('a2a-run')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open run' })).toHaveAttribute(
      'href',
      '/volundr/sessions/run-1',
    );
    expect(screen.getByRole('link', { name: 'Open coordinator' })).toHaveAttribute(
      'href',
      '/volundr/sessions/coordinator-1',
    );
  });

  it('shows what the workflow defines when its band is opened', () => {
    renderBoard(detail, service);

    fireEvent.click(screen.getByRole('button', { name: /Reviewed Delivery Flow/ }));

    const panel = screen.getByRole('complementary', { name: 'Reviewed Delivery Flow' });
    expect(within(panel).getByText('Building → Code review → Merging → Done')).toBeInTheDocument();
    expect(within(panel).getByText('A person approves')).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: 'Open workflow' })).toBeInTheDocument();
    expect(within(panel).queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('shows where each workflow leads', () => {
    renderBoard(detail, service);

    fireEvent.click(screen.getByRole('button', { name: /Ticket Planning Flow/ }));

    const panel = screen.getByRole('complementary', { name: 'Ticket Planning Flow' });
    expect(
      within(panel).getByText('Planning → Plan review → Recording plan → Building'),
    ).toBeInTheDocument();
  });

  it('shows the step a run is on', () => {
    const running: WorkTask = {
      ...detail.tasks[2]!,
      board: { columnId: 'building', state: 'running', gate: null },
      workflowRun: {
        taskId: 'a2a-run',
        workflowName: 'Reviewed Delivery Flow',
        status: 'running',
        sessionId: 'run-1',
        currentStep: 'Implement tracker ticket',
      },
    };
    renderBoard({ ...detail, tasks: [running] }, service);

    expect(screen.getByText('Implement tracker ticket')).toBeInTheDocument();
  });

  it('closes the panel', () => {
    renderBoard(detail, service);
    openGate();

    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));

    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
  });

  it('says why a move is refused', () => {
    renderBoard(detail, service);

    drag('demo-issue-214', 'planning');

    expect(screen.getByRole('status')).toHaveTextContent('PLT-214 is not ready to start.');
  });

  it('moves the board sideways with a plain mouse wheel', () => {
    renderBoard(detail, service);
    const scroller = screen.getByTestId('board-scroller');

    fireEvent.wheel(scroller, { deltaY: 120 });

    expect(scroller.scrollLeft).toBe(120);
  });

  it('leaves a trackpad swipe to the browser', () => {
    renderBoard(detail, service);
    const scroller = screen.getByTestId('board-scroller');

    fireEvent.wheel(scroller, { deltaX: 120, deltaY: 10 });

    expect(scroller.scrollLeft).toBe(0);
  });

  it('lets a column with more tickets than fit scroll itself', () => {
    renderBoard(detail, service);
    const scroller = screen.getByTestId('board-scroller');
    const cards = screen.getByTestId('board-ticket-demo-issue-214').parentElement!;
    Object.defineProperty(cards, 'scrollHeight', { value: 500 });
    Object.defineProperty(cards, 'clientHeight', { value: 200 });

    fireEvent.wheel(cards, { deltaY: 120 });

    expect(scroller.scrollLeft).toBe(0);
  });

  it('says why a ticket is blocked', () => {
    const blocked: WorkTask = {
      ...detail.tasks[2]!,
      board: {
        columnId: 'building',
        state: 'blocked',
        gate: null,
        reason: 'The session’s agent stopped with an error.',
      },
    };
    renderBoard({ ...detail, tasks: [blocked] }, service);

    const building = screen.getByTestId('board-column-building');
    expect(within(building).getByText('Blocked')).toBeInTheDocument();
    expect(within(building).getByText(/stopped with an error/)).toBeInTheDocument();
  });

  it('names tickets it cannot place', () => {
    const lost: WorkTask = {
      ...detail.tasks[0]!,
      board: { columnId: null, state: 'unknown', gate: null },
    };
    renderBoard({ ...detail, tasks: [lost] }, service);

    expect(screen.getByRole('status')).toHaveTextContent('Ting cannot read where PLT-214 is');
  });
});
