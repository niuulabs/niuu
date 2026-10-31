import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { ExternalLink } from 'lucide-react';
import { ApiClientError } from '@niuulabs/query';
import type { BoardDecision, WorkBoardColumn, WorkBoardLayout, WorkTask } from '../domain/work';
import { BOARD_STATE_LABEL } from '../domain/workBoard';
import { CoordinatorChat } from './CoordinatorChat';
import { useGateDecision } from './useWork';

const QUESTION: Record<NonNullable<WorkBoardColumn['review']>, string> = {
  plan: 'Is this the right plan?',
  code: 'Is this ready to merge?',
  output: 'Is this finished?',
};

export type GateIntent = 'approve' | 'requestChanges' | null;
type PanelTab = 'coordinator' | 'review' | 'workflow';

/** The client's own message is only the status; the reason is in the response detail. */
export function decisionError(error: unknown): string {
  if (error instanceof ApiClientError && typeof error.detail === 'string' && error.detail) {
    return error.detail;
  }
  return error instanceof Error ? error.message : 'The decision did not go through';
}

const DECISION_LABEL: Record<string, string> = {
  approved: 'Approved',
  changes_requested: 'Changes requested',
};

function DecisionRecord({ decision }: { decision: BoardDecision }) {
  return (
    <dl className="ting-board-facts">
      <dt>Last decision</dt>
      <dd>
        {decision.label}: {DECISION_LABEL[decision.status] ?? decision.status}
      </dd>
      <dt>Decided by</dt>
      <dd>{decision.decidedBy}</dd>
      {decision.notes ? (
        <>
          <dt>Comment</dt>
          <dd>{decision.notes}</dd>
        </>
      ) : null}
    </dl>
  );
}

function PullRequestLink({ task }: { task: WorkTask }) {
  const url = task.workflowRun?.prUrl ?? task.operationalRun?.prUrl;
  if (!url) return null;
  return (
    <div className="ting-board-panel__links">
      <a href={url} target="_blank" rel="noreferrer">
        Open pull request <ExternalLink aria-hidden="true" />
      </a>
    </div>
  );
}

function OpenReview({ projectId, task }: { projectId: string; task: WorkTask }) {
  if (!task.workflowRun) return null;
  return (
    <div className="ting-board-panel__links">
      <Link to={`/ting/review/${projectId}/${task.source.issueId}` as never}>
        Open the full review
      </Link>
    </div>
  );
}

function ReviewTab({
  board,
  projectId,
  task,
  column,
  intent,
  onDecided,
}: {
  board: WorkBoardLayout;
  projectId: string;
  task: WorkTask;
  column: WorkBoardColumn;
  intent: GateIntent;
  onDecided: () => void;
}) {
  const gate = task.board?.gate;
  const decide = useGateDecision(projectId);
  const [notes, setNotes] = useState('');
  const [needsNote, setNeedsNote] = useState(false);
  const noteField = useRef<HTMLTextAreaElement>(null);
  const approve = board.columns.find((item) => item.id === column.approveColumnId);
  const changes = board.columns.find((item) => item.id === column.changesColumnId);

  useEffect(() => {
    if (intent === 'requestChanges') noteField.current?.focus();
  }, [intent]);

  const lastDecision = task.board?.lastDecision;
  if (!gate) {
    return (
      <>
        <OpenReview projectId={projectId} task={task} />
        {lastDecision ? <DecisionRecord decision={lastDecision} /> : null}
        <PullRequestLink task={task} />
      </>
    );
  }
  const activeGate = gate;

  function submit(decision: 'approve' | 'request_changes') {
    if (decision === 'request_changes' && !notes.trim()) {
      setNeedsNote(true);
      noteField.current?.focus();
      return;
    }
    setNeedsNote(false);
    decide.mutate(
      {
        issueId: task.source.issueId,
        decision: {
          gateId: activeGate.id,
          activationId: activeGate.activationId,
          decision,
          notes: notes.trim(),
        },
      },
      { onSuccess: onDecided },
    );
  }

  return (
    <>
      <div className="ting-board-decision">
        <span className="ting-work-eyebrow">{gate.label} · waiting for you</span>
        <h4>{column.review ? QUESTION[column.review] : gate.condition}</h4>
        {column.review ? <p>{gate.condition}</p> : null}
        <p className="ting-board-decision__routes">
          {approve?.kind === 'tracker'
            ? 'Approve closes the ticket.'
            : `Approve continues to ${approve?.title}.`}{' '}
          Changes go back to {changes?.title} in the same run. A new review needs a new decision.
        </p>
      </div>
      <OpenReview projectId={projectId} task={task} />
      {gate.instructions ? <p className="ting-board-panel__text">{gate.instructions}</p> : null}
      {gate.summary ? <p className="ting-board-panel__text">{gate.summary}</p> : null}
      {lastDecision ? <DecisionRecord decision={lastDecision} /> : null}
      <PullRequestLink task={task} />
      {intent === 'approve' ? (
        <p className="ting-board-panel__ask">
          A drag never approves on its own. Approve below to move {task.source.identifier} to{' '}
          {approve?.title}.
        </p>
      ) : null}
      <label className="ting-board-panel__notes">
        <span>Comment</span>
        <textarea
          ref={noteField}
          rows={2}
          value={notes}
          placeholder="What should change?"
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
      {needsNote ? (
        <p className="ting-work-action-error" role="alert">
          Say what should change first.
        </p>
      ) : null}
      {decide.error ? (
        <p className="ting-work-action-error" role="alert">
          {decisionError(decide.error)}
        </p>
      ) : null}
      <div className="ting-board-panel__actions">
        <button type="button" disabled={decide.isPending} onClick={() => submit('request_changes')}>
          Request changes → {changes?.title}
        </button>
        <button
          type="button"
          className="ting-board-panel__approve"
          disabled={decide.isPending}
          onClick={() => submit('approve')}
        >
          Approve → {approve?.title}
        </button>
      </div>
    </>
  );
}

function SessionLink({ sessionId, label }: { sessionId: string; label: string }) {
  return <Link to={`/volundr/sessions/${sessionId}` as never}>{label}</Link>;
}

function WorkflowTab({ board, task }: { board: WorkBoardLayout; task: WorkTask }) {
  const placement = task.board;
  const run = task.workflowRun;
  const column = board.columns.find((item) => item.id === placement?.columnId);
  const runSessionId = run?.sessionId ?? task.operationalRun?.sessionId;
  return (
    <>
      <dl className="ting-board-facts">
        <dt>Workflow</dt>
        <dd>{run?.workflowName ?? column?.workflowName ?? 'None yet'}</dd>
        <dt>Stage</dt>
        <dd>{column?.title ?? 'Unknown'}</dd>
        <dt>State</dt>
        <dd>{placement ? BOARD_STATE_LABEL[placement.state] : 'Unknown'}</dd>
        <dt>Tracker</dt>
        <dd>{task.source.status}</dd>
        {run ? (
          <>
            <dt>Task</dt>
            <dd className="ting-board-facts__mono">{run.taskId}</dd>
          </>
        ) : null}
      </dl>
      <div className="ting-board-panel__links">
        <a href={task.source.url} target="_blank" rel="noreferrer">
          Open ticket <ExternalLink aria-hidden="true" />
        </a>
        {runSessionId ? <SessionLink sessionId={runSessionId} label="Open run" /> : null}
        {task.coordinator ? (
          <SessionLink sessionId={task.coordinator.sessionId} label="Open coordinator" />
        ) : null}
      </div>
    </>
  );
}

export function TicketPanel({
  board,
  projectId,
  task,
  intent,
  onClose,
}: {
  board: WorkBoardLayout;
  projectId: string;
  task: WorkTask;
  intent: GateIntent;
  onClose: () => void;
}) {
  const column = board.columns.find((item) => item.id === task.board?.columnId);
  const reviewing = Boolean(task.board?.gate && column?.kind === 'gate');
  const [chosenTab, setTab] = useState<PanelTab>(reviewing ? 'review' : 'coordinator');
  const reviewed = reviewing || Boolean(task.board?.lastDecision);
  const tab = chosenTab === 'review' && !reviewed ? 'coordinator' : chosenTab;
  const tabs: { id: PanelTab; label: string }[] = [
    { id: 'coordinator', label: 'Coordinator' },
    ...(reviewed ? [{ id: 'review' as const, label: 'Review' }] : []),
    { id: 'workflow', label: 'Workflow' },
  ];
  return (
    <aside
      className={`ting-board-panel${tab === 'coordinator' ? ' ting-board-panel--chat' : ''}`}
      aria-label={task.source.identifier}
    >
      <div className="ting-board-panel__head">
        <span className="ting-work-task__key">{task.source.identifier}</span>
        <button type="button" onClick={onClose}>
          Hide
        </button>
      </div>
      <h3>{task.title}</h3>
      <div className="ting-board-tabs" role="tablist" aria-label="Ticket details">
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'coordinator' ? (
        <CoordinatorChat coordinator={task.coordinator} name={task.source.identifier} />
      ) : null}
      {tab === 'review' && column ? (
        <ReviewTab
          board={board}
          projectId={projectId}
          task={task}
          column={column}
          intent={intent}
          onDecided={() => setTab('coordinator')}
        />
      ) : null}
      {tab === 'workflow' ? <WorkflowTab board={board} task={task} /> : null}
    </aside>
  );
}
