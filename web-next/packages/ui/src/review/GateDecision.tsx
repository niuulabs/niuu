import { useState, type ReactNode, type Ref } from 'react';
import './GateDecision.css';

export interface GateDecisionFact {
  label: string;
  value: string;
}

export interface GateDecisionProps {
  eyebrow?: string;
  title: string;
  summary?: string;
  facts?: readonly GateDecisionFact[];
  /** What each answer does, in the host's words. */
  hint?: ReactNode;
  note: string;
  onNoteChange: (note: string) => void;
  noteLabel?: string;
  notePlaceholder?: string;
  noteRef?: Ref<HTMLTextAreaElement>;
  /** Line comments that go with the note. */
  commentCount?: number;
  /** A request for changes must say what should change. */
  requireNote?: boolean;
  approveLabel?: string;
  changesLabel?: string;
  busy?: boolean;
  /** Decisions are held, for instance while an older revision is on screen. */
  disabled?: boolean;
  error?: string | null;
  onApprove: () => void;
  onRequestChanges: () => void;
}

/** The one place a person answers a workflow gate, wherever they meet it. */
export function GateDecision({
  eyebrow,
  title,
  summary,
  facts = [],
  hint,
  note,
  onNoteChange,
  noteLabel = 'Your note',
  notePlaceholder = 'What should change?',
  noteRef,
  commentCount = 0,
  requireNote = false,
  approveLabel = 'Approve',
  changesLabel = 'Request changes',
  busy = false,
  disabled = false,
  error,
  onApprove,
  onRequestChanges,
}: GateDecisionProps) {
  const [needsNote, setNeedsNote] = useState(false);
  const held = busy || disabled;

  function requestChanges() {
    if (requireNote && !note.trim() && commentCount === 0) {
      setNeedsNote(true);
      return;
    }
    setNeedsNote(false);
    onRequestChanges();
  }

  return (
    <section className="niuu-gate-decision" aria-label={title}>
      {eyebrow ? <span className="niuu-gate-decision__eyebrow">{eyebrow}</span> : null}
      <h3>{title}</h3>
      {summary ? <p>{summary}</p> : null}
      {facts.length > 0 ? (
        <dl>
          {facts.map((fact) => (
            <div key={fact.label}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {hint ? <div className="niuu-gate-decision__hint">{hint}</div> : null}
      <label className="niuu-gate-decision__note">
        <span>{noteLabel}</span>
        <textarea
          ref={noteRef}
          rows={4}
          value={note}
          placeholder={notePlaceholder}
          onChange={(event) => {
            setNeedsNote(false);
            onNoteChange(event.target.value);
          }}
        />
      </label>
      {commentCount > 0 ? (
        <p className="niuu-gate-decision__comments">
          {commentCount} line comment{commentCount === 1 ? '' : 's'} will go with your note.
        </p>
      ) : null}
      {needsNote ? (
        <p className="niuu-gate-decision__error" role="alert">
          Say what should change first.
        </p>
      ) : null}
      {error ? (
        <p className="niuu-gate-decision__error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="niuu-gate-decision__actions">
        <button type="button" disabled={held} onClick={requestChanges}>
          {changesLabel}
        </button>
        <button
          type="button"
          className="niuu-gate-decision__approve"
          disabled={held}
          onClick={onApprove}
        >
          {approveLabel}
        </button>
      </div>
    </section>
  );
}
