import { useEffect, useRef, useState } from 'react';
import { Link } from '@tanstack/react-router';
import { ExternalLink } from 'lucide-react';
import { ApiClientError } from '@niuulabs/query';
import { GateDecision } from '@niuulabs/ui';
import type { BoardDecision, WorkBoardColumn, WorkBoardLayout, WorkTask } from '../domain/work';
import { BOARD_STATE_LABEL } from '../domain/workBoard';
import { CoordinatorChat } from './CoordinatorChat';
import { useGateDecision } from './useWork';
import { Dockable } from './review/Dockable';
import { useReturnTo } from './review/reviewBack';

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

/** The run's review page: the same one Artifacts and Work open, with a way back here. */
function OpenReview({ task }: { task: WorkTask }) {
  const returnTo = useReturnTo();
  if (!task.workflowRun) return null;
  return (
    <div className="ting-board-panel__links">
      <Link
        to={'/ting/artifacts/$slug' as never}
        params={{ slug: task.workflowRun.taskId } as never}
        search={{ returnTo } as never}
      >
        Open the run
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
  const noteField = useRef<HTMLTextAreaElement>(null);
  const approve = board.columns.find((item) => item.id === column.approveColumnId);
  const changes = board.columns.find((item) => item.id === column.changesColumnId);
  // A step can stop and ask (a plan that cannot be presented, say) outside the board's own
  // gate columns: the run continues or goes back where the workflow says, not to a column.
  const routed = column.kind === 'gate';

  useEffect(() => {
    if (intent === 'requestChanges') noteField.current?.focus();
  }, [intent]);

  const lastDecision = task.board?.lastDecision;
  if (!gate) {
    return (
      <>
        <OpenReview task={task} />
        {lastDecision ? <DecisionRecord decision={lastDecision} /> : null}
        <PullRequestLink task={task} />
      </>
    );
  }
  const activeGate = gate;

  function submit(decision: 'approve' | 'request_changes') {
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
      <OpenReview task={task} />
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
      <GateDecision
        eyebrow={`${gate.label} · waiting for you`}
        title={column.review ? QUESTION[column.review] : gate.condition}
        summary={column.review ? gate.condition : undefined}
        hint={
          routed
            ? `${
                approve?.kind === 'tracker'
                  ? 'Approve closes the ticket.'
                  : `Approve continues to ${approve?.title}.`
              } Changes go back to ${changes?.title} in the same run. A new review needs a new decision.`
            : 'Approve lets the run continue. Changes go back to the agent in the same run, with your comment.'
        }
        note={notes}
        onNoteChange={setNotes}
        noteLabel="Comment"
        noteRef={noteField}
        requireNote
        approveLabel={routed ? `Approve → ${approve?.title}` : 'Approve'}
        changesLabel={routed ? `Request changes → ${changes?.title}` : 'Request changes'}
        busy={decide.isPending}
        error={decide.error ? decisionError(decide.error) : null}
        onApprove={() => submit('approve')}
        onRequestChanges={() => submit('request_changes')}
      />
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
        {task.coordinator?.sessionId ? (
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
  floating,
  onFloatingChange,
  onClose,
}: {
  board: WorkBoardLayout;
  projectId: string;
  task: WorkTask;
  intent: GateIntent;
  /** Popped out, the panel floats over the board and the board gets the width back. */
  floating: boolean;
  onFloatingChange: (floating: boolean) => void;
  onClose: () => void;
}) {
  const column = board.columns.find((item) => item.id === task.board?.columnId);
  // Any gate the ticket waits at is decided here, in a gate column or not.
  const reviewing = Boolean(task.board?.gate);
  const [chosenTab, setTab] = useState<PanelTab>(reviewing ? 'review' : 'coordinator');
  const reviewed = reviewing || Boolean(task.board?.lastDecision);
  const tab = chosenTab === 'review' && !reviewed ? 'coordinator' : chosenTab;
  const tabs: { id: PanelTab; label: string }[] = [
    { id: 'coordinator', label: 'Coordinator' },
    ...(reviewed ? [{ id: 'review' as const, label: 'Review' }] : []),
    { id: 'workflow', label: 'Workflow' },
  ];
  return (
    <Dockable
      label={task.source.identifier}
      floating={floating}
      onFloatingChange={onFloatingChange}
      className={`ting-board-panel${tab === 'coordinator' ? ' ting-board-panel--chat' : ''}`}
      actions={
        <button type="button" className="ting-dockable__hide" onClick={onClose}>
          Hide
        </button>
      }
    >
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
    </Dockable>
  );
}
