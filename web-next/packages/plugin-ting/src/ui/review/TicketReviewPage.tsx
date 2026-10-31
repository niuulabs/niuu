import './TicketReviewPage.css';
import { useState } from 'react';
import { Link, useParams } from '@tanstack/react-router';
import { ExternalLink } from 'lucide-react';
import {
  addQuoteToNote,
  isPending,
  openingReview,
  type ReviewRevision,
  type RunReview,
} from '../../domain/review';
import type { WorkBoardLayout, WorkTask } from '../../domain/work';
import { CoordinatorChat } from '../CoordinatorChat';
import { decisionError } from '../TicketPanel';
import { useGateDecision, useTicketReviews, useWorkDetail } from '../useWork';
import { ReviewPanelSlot } from './reviewPanels';

const DECIDED_LABEL: Record<string, string> = {
  approved: 'Approved',
  changes_requested: 'Changes requested',
};

function statusLabel(revision: ReviewRevision): string {
  return isPending(revision)
    ? 'Waiting for you'
    : (DECIDED_LABEL[revision.status] ?? revision.status);
}

function GateHistory({ revisions }: { revisions: readonly ReviewRevision[] }) {
  return (
    <ol className="ting-review-history">
      {revisions.map((revision) => (
        <li key={revision.gateId}>
          <strong>
            r{revision.number} · {statusLabel(revision)}
          </strong>
          {revision.decidedBy ? <span>by {revision.decidedBy}</span> : null}
          {revision.notes ? <blockquote>{revision.notes}</blockquote> : null}
        </li>
      ))}
    </ol>
  );
}

function ReviewWorkspace({
  projectId,
  task,
  board,
  reviews,
}: {
  projectId: string;
  task: WorkTask;
  board: WorkBoardLayout | null;
  reviews: readonly RunReview[];
}) {
  const [chosenColumn, setChosenColumn] = useState<string | null>(null);
  const [chosenRevision, setChosenRevision] = useState<number | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  const [note, setNote] = useState('');
  const [needsNote, setNeedsNote] = useState(false);
  const [selection, setSelection] = useState('');
  const [asking, setAsking] = useState<string | null>(null);
  const decide = useGateDecision(projectId);

  const review = reviews.find((item) => item.columnId === chosenColumn) ?? openingReview(reviews);
  const latest = review?.revisions[review.revisions.length - 1];
  if (!review || !latest) return null;
  const revision = review.revisions.find((item) => item.number === chosenRevision) ?? latest;
  const previous = review.revisions.find((item) => item.number === revision.number - 1) ?? null;

  const gate = task.board?.gate ?? null;
  const decidable = gate !== null && gate.id === latest.gateId && isPending(latest);
  const reading = revision.number === latest.number;
  const canAsk = task.coordinator?.status === 'active' && Boolean(task.coordinator.chatEndpoint);
  const column = board?.columns.find((item) => item.id === review.columnId);
  const approve = board?.columns.find((item) => item.id === column?.approveColumnId);
  const changes = board?.columns.find((item) => item.id === column?.changesColumnId);
  const path = board?.columns.filter((item) => item.workflowId === column?.workflowId) ?? [];
  const pullRequestUrl = task.workflowRun?.prUrl ?? task.operationalRun?.prUrl ?? null;

  function readSelection() {
    setSelection(window.getSelection()?.toString().trim() ?? '');
  }

  function submit(decision: 'approve' | 'request_changes') {
    if (!gate) return;
    if (decision === 'request_changes' && !note.trim()) {
      setNeedsNote(true);
      return;
    }
    setNeedsNote(false);
    decide.mutate(
      {
        issueId: task.source.issueId,
        decision: {
          gateId: gate.id,
          activationId: gate.activationId,
          decision,
          notes: note.trim(),
        },
      },
      { onSuccess: () => setNote('') },
    );
  }

  return (
    <div className="ting-review">
      <header className="ting-review__head">
        <div className="ting-review__title">
          <div>
            <span className="ting-work-task__key">{task.source.identifier}</span>
            <h1>{task.title}</h1>
          </div>
          <nav className="ting-review__path" aria-label="Workflow">
            <span>{review.workflowName}</span>
            {path.map((item) => (
              <span key={item.id} aria-current={item.id === review.columnId ? 'step' : undefined}>
                {item.title}
              </span>
            ))}
          </nav>
        </div>
        <span className="ting-review__status">{statusLabel(revision)}</span>
        {reviews.length > 1 ? (
          <div className="ting-review__switch" role="tablist" aria-label="Reviews">
            {reviews.map((item) => (
              <button
                key={`${item.taskId}:${item.columnId}`}
                type="button"
                role="tab"
                aria-selected={item.columnId === review.columnId}
                onClick={() => {
                  setChosenColumn(item.columnId);
                  setChosenRevision(null);
                }}
              >
                {item.title}
              </button>
            ))}
          </div>
        ) : null}
        <div className="ting-review__switch" role="tablist" aria-label="Revisions">
          {review.revisions.map((item) => (
            <button
              key={item.gateId}
              type="button"
              role="tab"
              aria-selected={item.number === revision.number}
              onClick={() => setChosenRevision(item.number)}
            >
              r{item.number}
            </button>
          ))}
        </div>
        {previous ? (
          <button
            type="button"
            className="ting-review__toggle"
            aria-pressed={showChanges}
            onClick={() => setShowChanges((shown) => !shown)}
          >
            {showChanges ? 'Hide changes' : `Show changes since r${previous.number}`}
          </button>
        ) : null}
        {decidable ? (
          <div className="ting-review__actions">
            <button
              type="button"
              disabled={decide.isPending || !reading}
              onClick={() => submit('request_changes')}
            >
              Request changes → {changes?.title}
            </button>
            <button
              type="button"
              className="ting-review__approve"
              disabled={decide.isPending || !reading}
              onClick={() => submit('approve')}
            >
              Approve → {approve?.title}
            </button>
          </div>
        ) : null}
      </header>

      {decide.error ? (
        <p className="ting-review__error" role="alert">
          {decisionError(decide.error)}
        </p>
      ) : null}

      <div className="ting-review__columns">
        <aside className="ting-review__ticket" aria-label="Ticket">
          <h2>Ticket</h2>
          {task.description ? <p>{task.description}</p> : null}
          <a href={task.source.url} target="_blank" rel="noreferrer">
            Open ticket <ExternalLink aria-hidden="true" />
          </a>
          <h2>Gate history</h2>
          <GateHistory revisions={review.revisions} />
        </aside>

        <main className="ting-review__subject">
          {selection && (canAsk || decidable) ? (
            <div className="ting-review__selection" role="group" aria-label="Selected text">
              {canAsk ? (
                <button
                  type="button"
                  onClick={() => {
                    setAsking(selection);
                    setSelection('');
                  }}
                >
                  Ask the coordinator
                </button>
              ) : null}
              {decidable ? (
                <button
                  type="button"
                  onClick={() => {
                    setNote((current) => addQuoteToNote(current, selection));
                    setNeedsNote(false);
                    setSelection('');
                  }}
                >
                  Add to my note
                </button>
              ) : null}
            </div>
          ) : null}
          <div onMouseUp={readSelection} onKeyUp={readSelection}>
            {review.panels.map((panel) => (
              <ReviewPanelSlot
                key={`${panel.widget}:${panel.title}`}
                panel={panel}
                revision={revision}
                compareWith={showChanges ? previous : null}
                pullRequestUrl={pullRequestUrl}
              />
            ))}
          </div>
        </main>

        <aside className="ting-review__talk" aria-label="Talk it through">
          {decidable ? (
            <label className="ting-review__note">
              <span>Your note</span>
              <textarea
                rows={6}
                value={note}
                placeholder="Anything you want changed. Goes with Request changes."
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
          ) : null}
          {needsNote ? (
            <p className="ting-review__error" role="alert">
              Say what should change first.
            </p>
          ) : null}
          <CoordinatorChat
            coordinator={task.coordinator}
            name={task.source.identifier}
            quote={asking}
            onQuoteDone={() => setAsking(null)}
          />
        </aside>
      </div>
    </div>
  );
}

/** One ticket's reviews: what its workflows want a person to look at, and the agent beside it. */
export function TicketReviewPage() {
  const { projectId, issueId } = useParams({ strict: false }) as {
    projectId: string;
    issueId: string;
  };
  const detail = useWorkDetail('project', projectId);
  const record = useTicketReviews(projectId, issueId);

  if (detail.isLoading || record.isLoading) {
    return <p className="ting-review__state">Loading review…</p>;
  }
  const failure = detail.error ?? record.error;
  if (failure) {
    return (
      <p className="ting-review__state" role="alert">
        {failure instanceof Error ? failure.message : 'This review could not be read.'}
      </p>
    );
  }
  const task = detail.data?.tasks.find((item) => item.source.issueId === issueId);
  if (!task) {
    return (
      <p className="ting-review__state" role="alert">
        This ticket is not on the project.
      </p>
    );
  }
  const reviews = record.data?.reviews ?? [];
  const unreadable = record.data?.unreadableRuns ?? [];
  return (
    <div className="ting-review-page">
      {unreadable.length > 0 ? (
        <p className="ting-review__error" role="alert">
          The session of {unreadable.join(', ')} is gone, so its review cannot be read.
        </p>
      ) : null}
      {reviews.length > 0 ? (
        <ReviewWorkspace
          projectId={projectId}
          task={task}
          board={detail.data?.board ?? null}
          reviews={reviews}
        />
      ) : (
        <p className="ting-review__state">
          Nothing to review on {task.source.identifier} yet.{' '}
          <Link to={`/ting/work/project:${projectId}` as never}>Back to the board</Link>
        </p>
      )}
    </div>
  );
}
