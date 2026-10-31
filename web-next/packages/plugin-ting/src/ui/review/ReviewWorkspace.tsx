import './TicketReviewPage.css';
import { useCallback, useMemo, useRef, useState, type ReactNode } from 'react';
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
import type { GateDecision as GateAnswer, WorkBoardLayout } from '../../domain/work';
import { decisionError } from '../TicketPanel';
import { ReviewPanelSlot } from './reviewPanels';

const DECIDED_LABEL: Record<string, string> = {
  approved: 'Approved',
  changes_requested: 'Changes requested',
};

/** What is under review: a ticket on a board, or a run that has no ticket. */
export interface ReviewSubject {
  /** The ticket's key; a run has none. */
  identifier: string | null;
  title: string;
  /** The gate a person can answer now. */
  gate: { id: string; activationId: string } | null;
  pullRequestUrl: string | null;
  canAsk: boolean;
}

/** What the page's coordinator needs from the review around it. */
export interface ReviewTalk {
  quote: string | null;
  onQuoteDone: () => void;
  onQuotes: (quotes: string[]) => void;
}

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

/** The review itself. Each page brings what it knows about the thing being reviewed. */
export function ReviewWorkspace({
  subject,
  board,
  reviews,
  railLabel,
  rail,
  talk: renderTalk,
  deciding,
  decisionFailure,
  onDecide,
}: {
  subject: ReviewSubject;
  board: WorkBoardLayout | null;
  reviews: readonly RunReview[];
  railLabel: string;
  rail: ReactNode;
  talk?: (props: ReviewTalk) => ReactNode;
  deciding: boolean;
  decisionFailure: unknown;
  onDecide: (decision: GateAnswer, onSuccess: () => void) => void;
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
  const filesystem = useOptionalService<IFileSystemPort>('filesystem');

  const marks = useMemo(() => [...new Set([...chatQuotes, ...quotesIn(note)])], [chatQuotes, note]);
  const reportQuotes = useCallback((quotes: string[]) => {
    setChatQuotes((current) => (current.join('\n') === quotes.join('\n') ? current : quotes));
  }, []);
  const openQuote = useCallback((quote: string) => {
    const quoted = [...(talk.current?.querySelectorAll('blockquote') ?? [])].reverse();
    quoted.find((item) => item.textContent?.includes(quote))?.scrollIntoView({ block: 'center' });
  }, []);

  const quoteFromPage = useCallback((text: string) => {
    setNote((current) => addQuoteToNote(current, text));
  }, []);

  const review = reviews.find((item) => item.columnId === chosenColumn) ?? openingReview(reviews);
  const latest = review?.revisions[review.revisions.length - 1];
  if (!review || !latest) return null;
  const revision = review.revisions.find((item) => item.number === chosenRevision) ?? latest;
  const previous = review.revisions.find((item) => item.number === revision.number - 1) ?? null;

  const gate = subject.gate;
  const decidable = gate !== null && gate.id === latest.gateId && isPending(latest);
  const reading = revision.number === latest.number;
  const { canAsk, pullRequestUrl } = subject;
  const column = board?.columns.find((item) => item.id === review.columnId);
  const approve = board?.columns.find((item) => item.id === column?.approveColumnId);
  const changes = board?.columns.find((item) => item.id === column?.changesColumnId);
  const path = board?.columns.filter((item) => item.workflowId === column?.workflowId) ?? [];

  function readSelection() {
    setSelection(window.getSelection()?.toString().trim() ?? '');
  }

  function submit(decision: 'approve' | 'request_changes') {
    if (!gate) return;
    onDecide(
      {
        gateId: gate.id,
        activationId: gate.activationId,
        decision,
        notes: gateNotes(note, lineComments.comments),
      },
      () => {
        setNote('');
        lineComments.clear();
      },
    );
  }

  const panel = review.panels[chosenPanel] ?? review.panels[0];
  const pageContext = {
    ticket: subject.identifier ?? '',
    title: subject.title,
    review: review.title,
    revision: revision.number,
    status: revision.status,
  };
  const panelContent = panel ? (
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
      pageContext={pageContext}
      onQuote={decidable ? quoteFromPage : undefined}
      onAsk={canAsk ? setAsking : undefined}
    />
  ) : null;

  return (
    <div className="ting-review">
      <header className="ting-review__head">
        <div className="ting-review__title">
          <div>
            <span className="ting-work-task__key">{subject.identifier ?? 'Run'}</span>
            <h1 title={subject.title}>{subject.title}</h1>
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
        <aside className="ting-review__ticket" aria-label={railLabel}>
          <h2>{railLabel}</h2>
          {rail}
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
                {panelContent}
              </SessionResources>
            ) : (
              panelContent
            )}
          </div>
        </main>

        <aside ref={talk} className="ting-review__talk" aria-label="Talk it through">
          {decidable ? (
            <GateDecision
              title={review.title}
              hint={
                approve && changes
                  ? `Approving continues to ${approve.title}. Changes go back to ${changes.title} in the same run.`
                  : 'Approving continues the run. Changes go back to the agent in the same run.'
              }
              note={note}
              onNoteChange={setNote}
              notePlaceholder="Anything you want changed."
              commentCount={lineComments.comments.length}
              requireNote
              approveLabel={approve ? `Approve → ${approve.title}` : 'Approve'}
              changesLabel={changes ? `Request changes → ${changes.title}` : 'Request changes'}
              busy={deciding}
              disabled={!reading}
              error={decisionFailure ? decisionError(decisionFailure) : null}
              onApprove={() => submit('approve')}
              onRequestChanges={() => submit('request_changes')}
            />
          ) : null}
          {renderTalk?.({
            quote: asking,
            onQuoteDone: () => setAsking(null),
            onQuotes: reportQuotes,
          })}
        </aside>
      </div>
    </div>
  );
}
