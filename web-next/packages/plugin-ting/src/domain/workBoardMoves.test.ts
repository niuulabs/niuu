import { describe, expect, it } from 'vitest';
import type { BoardPlacement, WorkBoardColumn, WorkBoardLayout, WorkTask } from './work';
import { groupTasksByBoardColumn, resolveBoardMove } from './workBoard';

function column(
  id: string,
  kind: WorkBoardColumn['kind'],
  routes: Partial<WorkBoardColumn> = {},
): WorkBoardColumn {
  return {
    id,
    title: id[0]!.toUpperCase() + id.slice(1).replace('-', ' '),
    kind,
    review: null,
    approveColumnId: null,
    changesColumnId: null,
    ...routes,
  };
}

const planning = { workflowId: 'plan', workflowName: 'Ticket Planning Flow' };
const delivery = { workflowId: 'deliver', workflowName: 'Reviewed Delivery Flow' };
const deliveryColumns = [
  column('building', 'work', delivery),
  column('code-review', 'gate', {
    ...delivery,
    review: 'code',
    approveColumnId: 'merging',
    changesColumnId: 'building',
  }),
  column('merging', 'work', delivery),
];

const board: WorkBoardLayout = {
  columns: [column('backlog', 'tracker'), ...deliveryColumns, column('done', 'tracker')],
};

const planned: WorkBoardLayout = {
  columns: [
    column('backlog', 'tracker'),
    column('planning', 'work', planning),
    column('plan-review', 'gate', {
      ...planning,
      review: 'plan',
      approveColumnId: 'building',
      changesColumnId: 'planning',
    }),
    ...deliveryColumns,
    column('done', 'tracker'),
  ],
};

function idle(
  columnId: string,
  state: BoardPlacement['state'],
  passedWorkflowIds: string[] = [],
): Partial<WorkTask> {
  return { board: { columnId, state, gate: null, passedWorkflowIds } };
}

function task(columnId: string | null, overrides: Partial<WorkTask> = {}): WorkTask {
  const state = columnId === 'backlog' ? 'notStarted' : 'running';
  const placement: BoardPlacement = { columnId, state, gate: null };
  return {
    source: {
      connectionId: 'tracker',
      provider: 'linear',
      projectId: 'project',
      url: 'https://linear.example/PLT-1',
      status: 'Started',
      issueId: `issue-${columnId}`,
      identifier: 'PLT-1',
      statusType: 'started',
    },
    title: 'A ticket',
    operationalRun: null,
    dispatch: null,
    actions: [],
    board: placement,
    ...overrides,
  };
}

const dispatch = {
  sagaId: 'saga',
  issueId: 'issue',
  repo: 'niuulabs/niuu',
  connectionId: null,
  workflowId: null,
};

describe('resolveBoardMove', () => {
  it('does nothing when a ticket lands where it already is', () => {
    expect(resolveBoardMove(board, task('building'), 'building')).toEqual({ kind: 'none' });
  });

  it('starts a ready ticket dragged into the first column', () => {
    expect(resolveBoardMove(board, task('backlog', { dispatch }), 'building')).toEqual({
      kind: 'start',
      columnId: 'building',
    });
  });

  it('refuses to start a ticket that is not ready', () => {
    expect(resolveBoardMove(board, task('backlog'), 'building')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 is not ready to start. Check its tracker status and dependencies.',
    });
  });

  it('refuses a ticket dropped into the middle of the workflow', () => {
    expect(resolveBoardMove(board, task('backlog', { dispatch }), 'merging')).toEqual({
      kind: 'refuse',
      message: 'Reviewed Delivery Flow starts at Building.',
    });
  });

  it('never lets a drag land in a gate column', () => {
    expect(resolveBoardMove(board, task('building'), 'code-review')).toEqual({
      kind: 'refuse',
      message: 'Tickets reach Code review when the work before it finishes.',
    });
  });

  it('turns a drag out of a gate into that gate’s two answers', () => {
    expect(resolveBoardMove(board, task('code-review'), 'merging')).toEqual({ kind: 'approve' });
    expect(resolveBoardMove(board, task('code-review'), 'building')).toEqual({
      kind: 'requestChanges',
    });
  });

  it('refuses a drag from a gate that matches neither answer', () => {
    expect(resolveBoardMove(board, task('code-review'), 'done')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 has to pass Code review first.',
    });
  });

  it('refuses to pass a gate', () => {
    expect(resolveBoardMove(board, task('building'), 'done')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 has to pass Code review first.',
    });
  });

  it('leaves the forward move to the run after the last gate', () => {
    expect(resolveBoardMove(board, task('merging'), 'done')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 moves on when Merging finishes.',
    });
  });

  it('only sends a ticket back through a decision', () => {
    expect(resolveBoardMove(board, task('merging'), 'building')).toEqual({
      kind: 'refuse',
      message: 'Inside a run, only a decision sends a ticket back.',
    });
  });

  it('leaves a finished ticket alone', () => {
    expect(resolveBoardMove(board, task('done'), 'backlog')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 is done.',
    });
  });

  it('asks first when a drop would skip a workflow', () => {
    expect(resolveBoardMove(planned, task('backlog', { dispatch }), 'building')).toEqual({
      kind: 'skip',
      columnId: 'building',
      skipped: ['Ticket Planning Flow'],
    });
  });

  it('starts the first workflow of several without asking', () => {
    expect(resolveBoardMove(planned, task('backlog', { dispatch }), 'planning')).toEqual({
      kind: 'start',
      columnId: 'planning',
    });
  });

  it('refuses a drop into the middle of a later workflow', () => {
    expect(resolveBoardMove(planned, task('backlog', { dispatch }), 'merging')).toEqual({
      kind: 'refuse',
      message: 'Reviewed Delivery Flow starts at Building.',
    });
  });

  it('starts a workflow the ticket has been cleared for', () => {
    const ready = task('backlog', { ...idle('backlog', 'notStarted', ['plan']), dispatch });
    expect(resolveBoardMove(planned, ready, 'building')).toEqual({
      kind: 'start',
      columnId: 'building',
    });
  });

  it('runs an earlier workflow again once no run is active', () => {
    const ready = task('building', idle('building', 'ready', ['plan']));
    expect(resolveBoardMove(planned, ready, 'planning')).toEqual({
      kind: 'start',
      columnId: 'planning',
    });
  });

  it('leaves a ready ticket out of the tracker columns', () => {
    const ready = task('building', idle('building', 'ready', ['plan']));
    expect(resolveBoardMove(planned, ready, 'done')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 stays here until a workflow moves it.',
    });
  });

  it('stops at the gate of the workflow the run is in', () => {
    expect(resolveBoardMove(planned, task('planning'), 'building')).toEqual({
      kind: 'refuse',
      message: 'PLT-1 has to pass Plan review first.',
    });
  });

  it('holds a blocked run but lets a failed one start again', () => {
    const blocked = idle('merging', 'blocked');
    expect(resolveBoardMove(planned, task('merging', blocked), 'building').kind).toBe('refuse');
    const failed: WorkTask = task('merging', {
      ...blocked,
      workflowRun: { taskId: 'run', workflowName: 'Delivery', status: 'failed', sessionId: null },
    });
    expect(resolveBoardMove(planned, failed, 'building')).toEqual({
      kind: 'start',
      columnId: 'building',
    });
  });

  it('refuses to move a ticket it cannot place', () => {
    expect(resolveBoardMove(board, task(null), 'building')).toEqual({
      kind: 'refuse',
      message: 'Ting cannot read where PLT-1 is right now.',
    });
  });
});

describe('groupTasksByBoardColumn', () => {
  it('keeps the run’s columns together and unreadable tickets unplaced', () => {
    const lost = task(null);
    const groups = groupTasksByBoardColumn(board, [task('backlog'), task('merging'), lost]);

    expect(groups.backlog.map((entry) => entry.items.length)).toEqual([1]);
    const [workflow] = groups.workflows;
    expect(workflow?.name).toBe('Reviewed Delivery Flow');
    expect(workflow?.columns.map((entry) => entry.id)).toEqual([
      'building',
      'code-review',
      'merging',
    ]);
    expect(workflow?.columns[2]?.items).toHaveLength(1);
    expect(groups.done.map((entry) => entry.id)).toEqual(['done']);
    expect(groups.unplaced).toEqual([lost]);
  });

  it('gives each workflow its own span of columns, in order', () => {
    const groups = groupTasksByBoardColumn(planned, []);

    expect(groups.workflows.map((workflow) => workflow.id)).toEqual(['plan', 'deliver']);
    expect(groups.workflows[0]?.columns.map((entry) => entry.id)).toEqual([
      'planning',
      'plan-review',
    ]);
  });
});
