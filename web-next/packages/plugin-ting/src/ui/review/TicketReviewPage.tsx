import './TicketReviewPage.css';
import { useCallback, useMemo, useRef, useState } from 'react';
import { Link, useParams } from '@tanstack/react-router';
import { ExternalLink } from 'lucide-react';
import { useOptionalService } from '@niuulabs/plugin-sdk';
import {
  GateDecision,
  SessionResources,
  gateNotes,
  useReviewComments,
  type IFileSystemPort,
} from '@niuulabs/ui';
import {
  addQuoteToNote,
  isPending,
  openingReview,
  quotesIn,
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
  planSlug,
}: {
  projectId: string;
  task: WorkTask;
  board: WorkBoardLayout | null;
  reviews: readonly RunReview[];
  planSlug?: string | null;
}) {
  const [chosenColumn, setChosenColumn] = useState<string | null>(null);
  const [chosenRevision, setChosenRevision] = useState<number | null>(null);
  const [showChanges, setShowChanges] = useState(false);
  const [chosenPanel, setChosenPanel] = useState(0);
  const [note, setNote] = useState('');
  const [selection, setSelection] = useState('');
  const [asking, setAsking] = useState<string | null>(null);
  const [chatQuotes, setChatQuotes] = useState<readonly string[]>([]);
  const talk = useRef<HTMLElement>(null);
  const lineComments = useReviewComments();
  const decide = useGateDecision(projectId);
  const filesystem = useOptionalService<IFileSystemPort>('filesystem');

  const marks = useMemo(() => [...new Set([...chatQuotes, ...quotesIn(note)])], [chatQuotes, note]);
  const reportQuotes = useCallback((quotes: string[]) => {
    setChatQuotes((current) => (current.join('\n') === quotes.join('\n') ? current : quotes));
  }, []);
  const openQuote = useCallback((quote: string) => {
    const quoted = [...(talk.current?.querySelectorAll('blockquote') ?? [])].reverse();
    quoted.find((item) => item.textContent?.includes(quote))?.scrollIntoView({ block: 'center' });
  }, []);

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
    decide.mutate(
      {
        issueId: task.source.issueId,
        decision: {
          gateId: gate.id,
          activationId: gate.activationId,
          decision,
          notes: gateNotes(note, lineComments.comments),
        },
      },
      {
        onSuccess: () => {
          setNote('');
          lineComments.clear();
        },
      },
    );
  }

  const panel = review.panels[chosenPanel] ?? review.panels[0];
  const subject = panel ? (
    <ReviewPanelSlot
      panel={panel}
      revision={revision}
      compareWith={showChanges ? previous : null}
      pullRequestUrl={pullRequestUrl}
      chatEndpoint={review.chatEndpoint ?? null}
      sessionId={review.sessionId}
      filesystem={filesystem ?? null}
      review={decidable ? lineComments : undefined}
      marks={marks}
      onMarkClick={openQuote}
    />
  ) : null;

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
                  setChosenPanel(0);
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
      </header>

      <div className="ting-review__columns">
        <aside className="ting-review__ticket" aria-label="Ticket">
          <h2>Ticket</h2>
          {task.description ? <p>{task.description}</p> : null}
          <a href={task.source.url} target="_blank" rel="noreferrer">
            Open ticket <ExternalLink aria-hidden="true" />
          </a>
          {planSlug ? (
            <Link to={`/ting/plan/${planSlug}` as never}>Open the project plan</Link>
          ) : null}
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
                    setSelection('');
                  }}
                >
                  Add to my note
                </button>
              ) : null}
            </div>
          ) : null}
          {review.panels.length > 1 ? (
            <div className="ting-review__switch" role="tablist" aria-label="Panels">
              {review.panels.map((item, index) => (
                <button
                  key={`${item.widget}:${item.title}`}
                  type="button"
                  role="tab"
                  aria-selected={item === panel}
                  onClick={() => setChosenPanel(index)}
                >
                  {item.title}
                </button>
              ))}
            </div>
          ) : null}
          <div className="ting-review__panel" onMouseUp={readSelection} onKeyUp={readSelection}>
            {filesystem ? (
              <SessionResources sessionId={review.sessionId} filesystem={filesystem}>
                {subject}
              </SessionResources>
            ) : (
              subject
            )}
          </div>
        </main>

        <aside ref={talk} className="ting-review__talk" aria-label="Talk it through">
          {decidable ? (
            <GateDecision
              title={review.title}
              hint={`Approving continues to ${approve?.title}. Changes go back to ${changes?.title} in the same run.`}
              note={note}
              onNoteChange={setNote}
              notePlaceholder="Anything you want changed."
              commentCount={lineComments.comments.length}
              requireNote
              approveLabel={`Approve → ${approve?.title}`}
              changesLabel={`Request changes → ${changes?.title}`}
              busy={decide.isPending}
              disabled={!reading}
              error={decide.error ? decisionError(decide.error) : null}
              onApprove={() => submit('approve')}
              onRequestChanges={() => submit('request_changes')}
            />
          ) : null}
          <CoordinatorChat
            coordinator={task.coordinator}
            name={task.source.identifier}
            quote={asking}
            onQuoteDone={() => setAsking(null)}
            onQuotes={reportQuotes}
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
          planSlug={record.data?.planSlug}
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
