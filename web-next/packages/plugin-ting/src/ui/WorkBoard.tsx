import { Link } from '@tanstack/react-router';
import { Columns3, ExternalLink, List } from 'lucide-react';
import { relTime } from '@niuulabs/ui';
import type { WorkSummary, WorkTask } from '../domain/work';
import {
  CONDITION_LABEL,
  groupTasksByStatus,
  groupWorkByCondition,
  KIND_LABEL,
  type BoardColumn,
  type WorkCondition,
  type WorkView,
} from '../domain/workBoard';

function conditionTone(condition: WorkCondition): string {
  if (condition === 'needsAttention' || condition === 'failed') return 'attention';
  if (condition === 'completed') return 'complete';
  if (condition === 'active') return 'active';
  return 'quiet';
}

export function WorkConditionBadge({ condition }: { condition: WorkCondition }) {
  return (
    <span className={`ting-work-condition ting-work-condition--${conditionTone(condition)}`}>
      <span aria-hidden="true" />
      {CONDITION_LABEL[condition]}
    </span>
  );
}

export function WorkViewToggle({
  view,
  onChange,
}: {
  view: WorkView;
  onChange: (view: WorkView) => void;
}) {
  return (
    <div className="ting-work-view-toggle" role="group" aria-label="Work layout">
      <button
        type="button"
        aria-label="List view"
        title="List view"
        aria-pressed={view === 'list'}
        onClick={() => onChange('list')}
      >
        <List aria-hidden="true" />
      </button>
      <button
        type="button"
        aria-label="Board view"
        title="Board view"
        aria-pressed={view === 'board'}
        onClick={() => onChange('board')}
      >
        <Columns3 aria-hidden="true" />
      </button>
    </div>
  );
}

function BoardColumnFrame<T>({
  column,
  emptyLabel,
  renderItem,
}: {
  column: BoardColumn<T>;
  emptyLabel: string;
  renderItem: (item: T) => React.ReactNode;
}) {
  const headingId = `work-board-${column.id}`;
  return (
    <section
      className="ting-work-board__column"
      aria-labelledby={headingId}
      data-testid={`board-column-${column.id}`}
    >
      <header>
        <h3 id={headingId}>{column.title}</h3>
        <span>{column.items.length}</span>
      </header>
      <div className="ting-work-board__cards">
        {column.items.length > 0 ? (
          column.items.map(renderItem)
        ) : (
          <p className="ting-work-board__empty">{emptyLabel}</p>
        )}
      </div>
    </section>
  );
}

function WorkCard({ item, onSelect }: { item: WorkSummary; onSelect: () => void }) {
  return (
    <button
      type="button"
      className="ting-work-card"
      onClick={onSelect}
      data-testid={`work-card-${item.id}`}
    >
      <strong>{item.title}</strong>
      <small>
        {KIND_LABEL[item.kind]}
        {item.source?.projectId ? ` · ${item.source.projectId}` : ''}
      </small>
      {item.attention ? (
        <span className="ting-work-card__attention">{item.attention.message}</span>
      ) : item.currentStep ? (
        <span className="ting-work-card__step">{item.currentStep}</span>
      ) : null}
      <span className="ting-work-card__footer">
        <WorkConditionBadge condition={item.condition} />
        <small>{item.updatedAt ? relTime(item.updatedAt) : '—'}</small>
      </span>
    </button>
  );
}

export function WorkBoard({
  items,
  hideEmptyColumns,
  onSelect,
}: {
  items: WorkSummary[];
  hideEmptyColumns: boolean;
  onSelect: (item: WorkSummary) => void;
}) {
  const columns = groupWorkByCondition(items).filter(
    (column) => !hideEmptyColumns || column.items.length > 0,
  );
  return (
    <div className="ting-work-board" data-testid="work-board">
      {columns.map((column) => (
        <BoardColumnFrame
          key={column.id}
          column={column}
          emptyLabel="Nothing here"
          renderItem={(item) => (
            <WorkCard key={item.id} item={item} onSelect={() => onSelect(item)} />
          )}
        />
      ))}
    </div>
  );
}

export function WorkTaskActions({
  task,
  dispatching,
  onDispatch,
}: {
  task: WorkTask;
  dispatching: boolean;
  onDispatch: (task: WorkTask) => void;
}) {
  if (!task.operationalRun?.sessionId && !task.dispatch) return null;
  return (
    <div className="ting-work-task__actions">
      {task.operationalRun?.sessionId ? (
        <Link to={`/volundr/sessions/${task.operationalRun.sessionId}` as never}>Open session</Link>
      ) : null}
      {task.dispatch ? (
        <button type="button" disabled={dispatching} onClick={() => onDispatch(task)}>
          {dispatching ? 'Starting…' : 'Start eligible work'}
        </button>
      ) : null}
    </div>
  );
}

export function tingRunLabel(task: WorkTask, operationalRunCoverageComplete: boolean): string {
  if (task.operationalRun) return task.operationalRun.status;
  return operationalRunCoverageComplete ? 'Not started' : 'Unknown · linkage unavailable';
}

function TicketCard({
  task,
  dispatching,
  operationalRunCoverageComplete,
  onDispatch,
}: {
  task: WorkTask;
  dispatching: boolean;
  operationalRunCoverageComplete: boolean;
  onDispatch: (task: WorkTask) => void;
}) {
  return (
    <article className="ting-work-card ting-work-card--ticket">
      <a href={task.source.url} target="_blank" rel="noreferrer" className="ting-work-task__key">
        {task.source.identifier}
        <ExternalLink aria-hidden="true" />
      </a>
      <strong>{task.title}</strong>
      <div className="ting-work-task__states">
        <span>
          Tracker <strong>{task.source.status}</strong>
        </span>
        <span>
          Ting <strong>{tingRunLabel(task, operationalRunCoverageComplete)}</strong>
        </span>
      </div>
      <WorkTaskActions task={task} dispatching={dispatching} onDispatch={onDispatch} />
    </article>
  );
}

export function TicketBoard({
  tasks,
  dispatchingIssueId,
  operationalRunCoverageComplete,
  onDispatch,
}: {
  tasks: WorkTask[];
  dispatchingIssueId: string | null;
  operationalRunCoverageComplete: boolean;
  onDispatch: (task: WorkTask) => void;
}) {
  return (
    <div className="ting-work-board" data-testid="ticket-board">
      {groupTasksByStatus(tasks).map((column) => (
        <BoardColumnFrame
          key={column.id}
          column={column}
          emptyLabel="No tickets"
          renderItem={(task) => (
            <TicketCard
              key={`${task.source.connectionId}:${task.source.issueId}`}
              task={task}
              dispatching={dispatchingIssueId === task.source.issueId}
              operationalRunCoverageComplete={operationalRunCoverageComplete}
              onDispatch={onDispatch}
            />
          )}
        />
      ))}
    </div>
  );
}
