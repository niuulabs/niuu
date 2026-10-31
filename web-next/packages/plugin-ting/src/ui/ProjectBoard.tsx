import { useRef, useState, type DragEvent, type ReactNode, type WheelEvent } from 'react';
import { ExternalLink } from 'lucide-react';
import { ApiClientError } from '@niuulabs/query';
import type { WorkBoardLayout, WorkTask } from '../domain/work';
import {
  BOARD_STATE_LABEL,
  canRetryStart,
  groupTasksByBoardColumn,
  resolveBoardMove,
  type ProjectBoardColumn,
} from '../domain/workBoard';
import { StagePanel } from './StagePanel';
import { TicketPanel, type GateIntent } from './TicketPanel';
import { useStartTicket } from './useWork';

const DRAG_EDGE_PX = 72;
const DRAG_SCROLL_PX = 28;

const STATE_TONE: Record<NonNullable<WorkTask['board']>['state'], string> = {
  notStarted: 'quiet',
  ready: 'attention',
  starting: 'active',
  running: 'active',
  needsDecision: 'attention',
  blocked: 'attention',
  done: 'complete',
  unknown: 'quiet',
};

function ticketId(task: WorkTask): string {
  return task.source.issueId;
}

function startError(error: unknown): string {
  if (error instanceof ApiClientError && typeof error.detail === 'string' && error.detail) {
    return error.detail;
  }
  return error instanceof Error ? error.message : 'This ticket could not be started';
}

function BoardCard({
  task,
  selected,
  starting,
  onSelect,
  onStart,
}: {
  task: WorkTask;
  selected: boolean;
  starting: boolean;
  onSelect: () => void;
  onStart: () => void;
}) {
  const placement = task.board;
  const retrying = canRetryStart(task);
  const canStart =
    retrying ||
    placement?.state === 'ready' ||
    (Boolean(task.dispatch) && placement?.state === 'notStarted');
  const coordinating = task.coordinator?.status === 'active';
  return (
    <article
      className={`ting-work-card ting-work-card--ticket${selected ? ' ting-board-card--selected' : ''}`}
      draggable
      data-testid={`board-ticket-${ticketId(task)}`}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', ticketId(task));
      }}
    >
      <a href={task.source.url} target="_blank" rel="noreferrer" className="ting-work-task__key">
        {task.source.identifier}
        <ExternalLink aria-hidden="true" />
      </a>
      <button
        type="button"
        className="ting-board-card__title"
        aria-pressed={selected}
        onClick={onSelect}
      >
        {task.title}
      </button>
      {placement ? (
        <span className={`ting-work-condition ting-work-condition--${STATE_TONE[placement.state]}`}>
          <span aria-hidden="true" />
          {BOARD_STATE_LABEL[placement.state]}
        </span>
      ) : null}
      {placement?.state === 'blocked' && (placement.reason || placement.gate) ? (
        <span className="ting-work-card__attention">
          {placement.reason ?? placement.gate?.label}
        </span>
      ) : null}
      {task.workflowRun?.currentStep && placement?.state === 'running' ? (
        <span className="ting-work-card__step">{task.workflowRun.currentStep}</span>
      ) : null}
      {coordinating ? (
        <span className="ting-board-card__coordinator">
          <span aria-hidden="true">C</span>
          Coordinator active
        </span>
      ) : null}
      {canStart ? (
        <div className="ting-work-task__actions">
          <button type="button" disabled={starting} onClick={onStart}>
            {starting ? 'Starting…' : retrying ? 'Retry' : 'Start'}
          </button>
        </div>
      ) : null}
    </article>
  );
}

function BoardColumnView({
  entry,
  onDropTicket,
  renderCard,
}: {
  entry: ProjectBoardColumn;
  onDropTicket: (issueId: string, columnId: string) => void;
  renderCard: (task: WorkTask) => ReactNode;
}) {
  const [over, setOver] = useState(false);
  const { column } = entry;

  function handleDrop(event: DragEvent) {
    event.preventDefault();
    setOver(false);
    onDropTicket(event.dataTransfer.getData('text/plain'), column.id);
  }

  return (
    <section
      className={`ting-work-board__column ting-board-column--${column.kind}${
        over ? ' ting-board-column--over' : ''
      }`}
      aria-label={column.title}
      data-testid={`board-column-${column.id}`}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={handleDrop}
    >
      <header>
        <h3>{column.title}</h3>
        <span>{entry.items.length}</span>
      </header>
      <div className="ting-work-board__cards">
        {entry.items.length > 0 ? (
          entry.items.map(renderCard)
        ) : (
          <p className="ting-work-board__empty">No tickets</p>
        )}
      </div>
    </section>
  );
}

interface PendingSkip {
  task: WorkTask;
  columnId: string;
  skipped: string[];
}

export function ProjectBoard({
  board,
  projectId,
  tasks,
}: {
  board: WorkBoardLayout;
  projectId: string;
  tasks: WorkTask[];
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [stageWorkflowId, setStageWorkflowId] = useState<string | null>(null);
  const [intent, setIntent] = useState<GateIntent>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingSkip, setPendingSkip] = useState<PendingSkip | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const start = useStartTicket(projectId);
  const groups = groupTasksByBoardColumn(board, tasks);
  const selected = tasks.find((task) => ticketId(task) === selectedId);

  function select(task: WorkTask) {
    setStageWorkflowId(null);
    setSelectedId(ticketId(task));
    setIntent(null);
    setNotice(null);
  }

  function startTicket(task: WorkTask, columnId?: string) {
    setNotice(null);
    setPendingSkip(null);
    setStageWorkflowId(null);
    setSelectedId(ticketId(task));
    start.mutate(
      { issueId: ticketId(task), ...(columnId ? { columnId } : {}) },
      { onError: (error) => setNotice(startError(error)) },
    );
  }

  function handleDrop(issueId: string, columnId: string) {
    const task = tasks.find((item) => ticketId(item) === issueId);
    if (!task) return;
    const move = resolveBoardMove(board, task, columnId);
    if (move.kind === 'none') return;
    setPendingSkip(null);
    if (move.kind === 'refuse') {
      setNotice(move.message);
      return;
    }
    setNotice(null);
    if (move.kind === 'start') {
      startTicket(task, move.columnId);
      return;
    }
    if (move.kind === 'skip') {
      setPendingSkip({ task, columnId: move.columnId, skipped: move.skipped });
      return;
    }
    setSelectedId(ticketId(task));
    setIntent(move.kind);
  }

  function scrollWhileDragging(event: DragEvent) {
    const element = scroller.current;
    if (!element) return;
    const edge = element.getBoundingClientRect();
    if (event.clientX > edge.right - DRAG_EDGE_PX) element.scrollLeft += DRAG_SCROLL_PX;
    if (event.clientX < edge.left + DRAG_EDGE_PX) element.scrollLeft -= DRAG_SCROLL_PX;
  }

  /** A plain mouse wheel only scrolls vertically, so it moves the board unless a column needs it. */
  function scrollWithWheel(event: WheelEvent) {
    const element = scroller.current;
    if (!element || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
    const cards = (event.target as Element).closest('.ting-work-board__cards');
    if (cards && cards.scrollHeight > cards.clientHeight) return;
    element.scrollLeft += event.deltaY;
  }

  function renderColumn(entry: ProjectBoardColumn) {
    return (
      <BoardColumnView
        key={entry.id}
        entry={entry}
        onDropTicket={handleDrop}
        renderCard={(task) => (
          <BoardCard
            key={ticketId(task)}
            task={task}
            selected={ticketId(task) === selectedId}
            starting={start.isPending && start.variables?.issueId === ticketId(task)}
            onSelect={() => select(task)}
            onStart={() => startTicket(task)}
          />
        )}
      />
    );
  }

  return (
    <div
      className={`ting-project-board${
        selected || stageWorkflowId !== null ? ' ting-project-board--panel' : ''
      }`}
      data-testid="project-board"
    >
      <div className="ting-project-board__main">
        {notice ? (
          <p className="ting-board-notice" role="status">
            {notice}
          </p>
        ) : null}
        {pendingSkip ? (
          <div className="ting-board-notice ting-board-confirm" role="alert">
            <span>
              This skips {pendingSkip.skipped.join(' and ')}. {pendingSkip.task.source.identifier}{' '}
              starts here without running {pendingSkip.skipped.length > 1 ? 'them' : 'it'}.
            </span>
            <button
              type="button"
              onClick={() => startTicket(pendingSkip.task, pendingSkip.columnId)}
            >
              Skip and start
            </button>
            <button type="button" onClick={() => setPendingSkip(null)}>
              Keep it here
            </button>
          </div>
        ) : null}
        <div
          className="ting-board-scroller"
          ref={scroller}
          data-testid="board-scroller"
          onDragOver={scrollWhileDragging}
          onWheel={scrollWithWheel}
        >
          {groups.backlog.map(renderColumn)}
          {groups.workflows.map((workflow) => (
            <div className="ting-board-run" key={workflow.id}>
              <button
                type="button"
                className="ting-board-run__band"
                aria-pressed={stageWorkflowId === workflow.id}
                onClick={() => {
                  setSelectedId(null);
                  setStageWorkflowId((open) => (open === workflow.id ? null : workflow.id));
                }}
              >
                <span>{workflow.name}</span>
                <small>One run</small>
              </button>
              <div className="ting-board-run__columns">{workflow.columns.map(renderColumn)}</div>
            </div>
          ))}
          {groups.done.map(renderColumn)}
        </div>
        {groups.unplaced.length > 0 ? (
          <p className="ting-board-notice" role="status">
            Ting cannot read where{' '}
            {groups.unplaced.map((task) => task.source.identifier).join(', ')}{' '}
            {groups.unplaced.length > 1 ? 'are' : 'is'} right now.
          </p>
        ) : null}
      </div>
      {stageWorkflowId !== null ? (
        <StagePanel
          board={board}
          workflowId={stageWorkflowId}
          onClose={() => setStageWorkflowId(null)}
        />
      ) : null}
      {selected ? (
        <TicketPanel
          key={`${ticketId(selected)}:${selected.board?.gate?.activationId ?? ''}:${intent ?? ''}`}
          board={board}
          projectId={projectId}
          task={selected}
          intent={intent}
          onClose={() => {
            setSelectedId(null);
            setIntent(null);
          }}
        />
      ) : null}
    </div>
  );
}
