import { describe, expect, it } from 'vitest';
import type { ReviewRevision, RunReview } from './review';
import { boardSteps, gateSteps } from './runSteps';
import type { WorkBoardColumn, WorkBoardLayout } from './work';

function column(id: string, workflowId: string | null): WorkBoardColumn {
  return {
    id,
    title: id,
    kind: 'work',
    review: null,
    approveColumnId: null,
    changesColumnId: null,
    workflowId,
  };
}

const board: WorkBoardLayout = {
  columns: [
    column('Backlog', null),
    column('Planning', 'plan'),
    column('Plan review', 'plan'),
    column('Recording plan', 'plan'),
    column('Building', 'build'),
  ],
};

function revision(status: string, requestedAt: string): ReviewRevision {
  return {
    number: 1,
    gateId: `gate-${requestedAt}`,
    activationId: 'a',
    requestedAt,
    status,
    summary: '',
    decidedBy: '',
    notes: '',
    decidedAt: '',
    fields: {},
  };
}

function review(columnId: string, revisions: ReviewRevision[]): RunReview {
  return {
    columnId,
    title: columnId,
    review: null,
    workflowName: 'Ticket Planning Flow',
    taskId: 'run-1',
    sessionId: 'session-1',
    panels: [],
    revisions,
  };
}

describe('boardSteps', () => {
  it("reads the workflow's columns against where the ticket waits", () => {
    const steps = boardSteps(board, 'plan', {
      columnId: 'Plan review',
      state: 'needsDecision',
      gate: { id: 'g', activationId: 'a' } as never,
    });
    expect(steps.map((step) => [step.title, step.state])).toEqual([
      ['Planning', 'done'],
      ['Plan review', 'waiting'],
      ['Recording plan', 'ahead'],
    ]);
  });

  it('names what the ticket is doing at a step with no gate', () => {
    const steps = boardSteps(board, 'plan', { columnId: 'Planning', state: 'running', gate: null });
    expect(steps[0]).toEqual({
      id: 'Planning',
      title: 'Planning',
      state: 'current',
      note: 'Running',
    });
  });

  it('shows a workflow the ticket has finished as done, and one it never entered as ahead', () => {
    const placement = { columnId: 'Building', state: 'running' as const, gate: null };
    expect(
      boardSteps(board, 'plan', { ...placement, passedWorkflowIds: ['plan'] }).map((s) => s.state),
    ).toEqual(['done', 'done', 'done']);
    expect(boardSteps(board, 'plan', placement).map((s) => s.state)).toEqual([
      'ahead',
      'ahead',
      'ahead',
    ]);
  });
});

describe('gateSteps', () => {
  it('lists the gates in the order the run reached them, as each was left', () => {
    const steps = gateSteps([
      review('Plan review', [revision('pending', '2026-11-04T05:00:00Z')]),
      review('Plan blocked', [revision('changes_requested', '2026-11-03T22:00:00Z')]),
      review('Never opened', []),
    ]);
    expect(steps).toEqual([
      { id: 'Plan blocked', title: 'Plan blocked', state: 'done', note: 'Changes requested' },
      { id: 'Plan review', title: 'Plan review', state: 'waiting', note: null },
    ]);
  });
});
