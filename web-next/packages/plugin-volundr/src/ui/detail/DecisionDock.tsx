import { useState, type ReactNode } from 'react';
import { ChevronDown, MessageSquareWarning, PauseCircle } from 'lucide-react';
import './DecisionDock.css';

/**
 * What the session waits on, kept by the composer as Lexi keeps its questions: a slim strip
 * ("1 decision waiting · Delivery blocked · Show") that opens into the decision in place, and
 * folds back. It never pushes the conversation down.
 */
export function DecisionDock({
  title,
  waiting,
  children,
}: {
  /** What the decision is about, e.g. the gate's label. */
  title: string;
  /** How long it has waited, e.g. "5h", when known. */
  waiting?: string;
  /** The decision itself: the reason, the notes and the answers. */
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <div className="forge-decision-strip" role="status" data-testid="decision-strip">
        <MessageSquareWarning className="forge-decision-strip__icon" aria-hidden="true" />
        <span className="forge-decision-strip__count">1 decision waiting</span>
        <span className="forge-decision-strip__title">{title}</span>
        <button
          type="button"
          className="forge-decision-strip__show"
          onClick={() => setOpen(true)}
          aria-label={`Show the decision: ${title}`}
        >
          Show
        </button>
      </div>
    );
  }

  return (
    <section className="forge-decision-card" aria-label="Your decision" data-testid="decision-card">
      <header className="forge-decision-card__head">
        <MessageSquareWarning className="forge-decision-strip__icon" aria-hidden="true" />
        <span className="forge-decision-card__heading">The workflow needs your decision</span>
        {waiting ? <span className="forge-decision-card__waiting">Waiting {waiting}</span> : null}
        <button
          type="button"
          className="forge-decision-card__fold"
          onClick={() => setOpen(false)}
          aria-label="Fold the decision back"
        >
          <ChevronDown aria-hidden="true" />
        </button>
      </header>
      {children}
    </section>
  );
}

/**
 * A workflow run held because its sessions are stopped, kept by the composer where the run's
 * other questions are: the step that waits, and starting the run's coordinator, which starts
 * every session of the run again.
 */
export function RunPausedStrip({
  step,
  busy,
  onStart,
}: {
  /** The step that waits, e.g. "Merge the change". */
  step: string;
  busy: boolean;
  onStart: () => void;
}) {
  return (
    <div className="forge-decision-strip" role="status" data-testid="run-paused-strip">
      <PauseCircle className="forge-decision-strip__icon" aria-hidden="true" />
      <span className="forge-decision-strip__count">Run paused</span>
      <span className="forge-decision-strip__title">
        {step} is ready, but the run&apos;s sessions are stopped
      </span>
      <button
        type="button"
        className="forge-decision-strip__show"
        onClick={onStart}
        disabled={busy}
        aria-label={`Start the run to carry on with ${step}`}
      >
        {busy ? 'Starting…' : 'Start the run'}
      </button>
    </div>
  );
}
