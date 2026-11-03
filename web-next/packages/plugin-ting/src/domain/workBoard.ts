import type {
  BoardTicketState,
  WorkBoardColumn,
  WorkBoardLayout,
  WorkSummary,
  WorkTask,
} from './work';

export type WorkView = 'list' | 'board';
export type WorkCondition = WorkSummary['condition'];

export const CONDITION_LABEL: Record<WorkCondition, string> = {
  needsAttention: 'Needs attention',
  active: 'In progress',
  waiting: 'Waiting',
  completed: 'Completed',
  failed: 'Failed',
  unknown: 'Unknown',
};

export const KIND_LABEL: Record<WorkSummary['kind'], string> = {
  project: 'Connected project',
  research: 'Research',
  specification: 'Specification',
  planning: 'Plan',
  workflowLaunch: 'Workflow',
  workflowExecution: 'Workflow execution',
};

export interface BoardColumn<T> {
  id: string;
  title: string;
  items: T[];
}

const WORK_COLUMNS: { id: string; title: string; conditions: WorkCondition[] }[] = [
  { id: 'attention', title: 'Needs attention', conditions: ['needsAttention', 'failed'] },
  { id: 'active', title: 'In progress', conditions: ['active', 'unknown'] },
  { id: 'waiting', title: 'Waiting', conditions: ['waiting'] },
  { id: 'completed', title: 'Completed', conditions: ['completed'] },
];

export function groupWorkByCondition(items: WorkSummary[]): BoardColumn<WorkSummary>[] {
  return WORK_COLUMNS.map(({ id, title, conditions }) => ({
    id,
    title,
    items: items.filter((item) => conditions.includes(item.condition)),
  }));
}

/** Optional columns only appear with tickets in them, so a typical board stays four wide. */
const TICKET_COLUMNS: { id: string; title: string; statusTypes: string[]; optional?: true }[] = [
  { id: 'backlog', title: 'Backlog', statusTypes: ['triage', 'backlog'] },
  { id: 'todo', title: 'To do', statusTypes: ['unstarted'] },
  { id: 'blocked', title: 'Blocked', statusTypes: ['blocked'], optional: true },
  { id: 'started', title: 'In progress', statusTypes: ['started'] },
  { id: 'completed', title: 'Done', statusTypes: ['completed'] },
  { id: 'canceled', title: 'Canceled', statusTypes: ['canceled'], optional: true },
];

function ticketStatusType(task: WorkTask): string {
  return task.source.statusType.trim().toLocaleLowerCase();
}

/**
 * Columns follow the tracker's status type, which Linear and Jira both report. A type no column
 * claims lands in Other so a new tracker state shows up rather than the ticket going missing.
 */
export function groupTasksByStatus(tasks: WorkTask[]): BoardColumn<WorkTask>[] {
  const known = new Set(TICKET_COLUMNS.flatMap((column) => column.statusTypes));
  const columns = TICKET_COLUMNS.map(({ id, title, statusTypes, optional }) => ({
    id,
    title,
    optional,
    items: tasks.filter((task) => statusTypes.includes(ticketStatusType(task))),
  }));
  const other = tasks.filter((task) => !known.has(ticketStatusType(task)));
  return [
    ...columns.filter((column) => !column.optional || column.items.length > 0),
    ...(other.length > 0 ? [{ id: 'other', title: 'Other', items: other }] : []),
  ].map(({ id, title, items }) => ({ id, title, items }));
}

export const BOARD_STATE_LABEL: Record<BoardTicketState, string> = {
  notStarted: 'Not started',
  ready: 'Ready to start',
  starting: 'Starting',
  running: 'Running',
  needsDecision: 'Needs you',
  blocked: 'Blocked',
  done: 'Done',
  unknown: 'Unknown',
};

export interface ProjectBoardColumn extends BoardColumn<WorkTask> {
  column: WorkBoardColumn;
}

/** One workflow's span of columns on the board. */
export interface BoardWorkflow<C> {
  id: string;
  name: string;
  columns: C[];
}

export interface ProjectBoardGroups {
  backlog: ProjectBoardColumn[];
  workflows: BoardWorkflow<ProjectBoardColumn>[];
  done: ProjectBoardColumn[];
  unplaced: WorkTask[];
}

/** Neighbouring columns of one workflow form its span, in board order. */
function spans<C>(columns: C[], columnOf: (entry: C) => WorkBoardColumn): BoardWorkflow<C>[] {
  const workflows: BoardWorkflow<C>[] = [];
  for (const entry of columns) {
    const column = columnOf(entry);
    if (column.kind === 'tracker') continue;
    const id = column.workflowId ?? '';
    const last = workflows.at(-1);
    if (last?.id === id) {
      last.columns.push(entry);
      continue;
    }
    workflows.push({ id, name: column.workflowName ?? '', columns: [entry] });
  }
  return workflows;
}

export function boardWorkflows(board: WorkBoardLayout): BoardWorkflow<WorkBoardColumn>[] {
  return spans(board.columns, (column) => column);
}

/** Tickets whose place could not be read stay visible and unplaced; they are never guessed. */
export function groupTasksByBoardColumn(
  board: WorkBoardLayout,
  tasks: WorkTask[],
): ProjectBoardGroups {
  const columns = board.columns.map((column) => ({
    id: column.id,
    title: column.title,
    column,
    items: tasks.filter((task) => task.board?.columnId === column.id),
  }));
  return {
    backlog: columns.filter(({ column }) => column.id === 'backlog'),
    workflows: spans(columns, ({ column }) => column),
    done: columns.filter(({ column }) => column.id === 'done'),
    unplaced: tasks.filter(
      (task) => !board.columns.some((column) => column.id === task.board?.columnId),
    ),
  };
}

export type BoardMove =
  | { kind: 'none' }
  | { kind: 'start'; columnId: string }
  | { kind: 'skip'; columnId: string; skipped: string[] }
  | { kind: 'approve' }
  | { kind: 'requestChanges' }
  | { kind: 'refuse'; message: string };

function refuse(message: string): BoardMove {
  return { kind: 'refuse', message };
}

/** The coordinator stopped before any run began, so Ting can replace it. */
function coordinatorDiedBeforeARun(task: WorkTask): boolean {
  return (
    task.board?.state === 'blocked' &&
    Boolean(task.coordinator) &&
    !task.workflowRun &&
    !task.operationalRun
  );
}

/** No run exists yet, so the coordinator can simply be asked again. */
export function canRetryStart(task: WorkTask): boolean {
  return (
    task.board?.state === 'starting' ||
    Boolean(task.coordinator?.startError) ||
    coordinatorDiedBeforeARun(task)
  );
}

function hasActiveRun(task: WorkTask): boolean {
  if (task.coordinator?.startError || coordinatorDiedBeforeARun(task)) return false;
  const state = task.board?.state;
  if (state === 'blocked') return task.workflowRun?.status !== 'failed';
  return state === 'starting' || state === 'running' || state === 'needsDecision';
}

/**
 * What dropping a ticket on a column means. A run moves its own ticket forward, so a person can
 * only answer the gate it is waiting on, or start a workflow at its first column once no run is
 * active. Neither the gate nor its routes live here.
 */
export function resolveBoardMove(
  board: WorkBoardLayout,
  task: WorkTask,
  targetColumnId: string,
): BoardMove {
  const key = task.source.identifier;
  const currentId = task.board?.columnId ?? null;
  if (currentId === targetColumnId) return { kind: 'none' };
  const current = board.columns.find((column) => column.id === currentId);
  const target = board.columns.find((column) => column.id === targetColumnId);
  if (!current || !target) return refuse(`Ting cannot read where ${key} is right now.`);
  if (current.id === 'done') return refuse(`${key} is done.`);
  if (current.kind === 'gate') {
    if (target.id === current.approveColumnId) return { kind: 'approve' };
    if (target.id === current.changesColumnId) return { kind: 'requestChanges' };
  }
  if (target.kind === 'gate') {
    return refuse(`Tickets reach ${target.title} when the work before it finishes.`);
  }

  const currentIndex = board.columns.indexOf(current);
  const forward = board.columns.indexOf(target) > currentIndex;
  if (hasActiveRun(task)) {
    if (!forward) return refuse('Inside a run, only a decision sends a ticket back.');
    const gate = board.columns
      .slice(currentIndex)
      .find((column) => column.kind === 'gate' && column.workflowId === current.workflowId);
    return refuse(
      gate
        ? `${key} has to pass ${gate.title} first.`
        : `${key} moves on when ${current.title} finishes.`,
    );
  }
  if (target.kind === 'tracker') return refuse(`${key} stays here until a workflow moves it.`);

  const workflows = boardWorkflows(board);
  const workflow = workflows.find(({ id }) => id === (target.workflowId ?? ''));
  const first = workflow?.columns[0];
  if (!workflow || !first) return refuse(`Ting cannot read where ${key} is right now.`);
  if (target.id !== first.id) return refuse(`${workflow.name} starts at ${first.title}.`);
  if (current.id === 'backlog' && !task.dispatch) {
    return refuse(`${key} is not ready to start. Check its tracker status and dependencies.`);
  }

  const passed = task.board?.passedWorkflowIds ?? [];
  const from = Math.max(
    workflows.findIndex(({ id }) => id === (current.workflowId ?? '')),
    0,
  );
  const skipped = forward
    ? workflows
        .slice(from, workflows.indexOf(workflow))
        .filter(({ id }) => !passed.includes(id))
        .map(({ name }) => name)
    : [];
  if (skipped.length > 0) return { kind: 'skip', columnId: target.id, skipped };
  return { kind: 'start', columnId: target.id };
}
