/**
 * ReviseFact — rewrite one fact in place.
 *
 * The old wording is never deleted: the backend appends the old → new
 * transition to the page's timeline, so the evidence trail keeps both. The
 * form writes as "you", which is what the timeline entry records.
 */

import { useState } from 'react';
import { Pencil } from 'lucide-react';
import { useRevisePage } from '../../application/useMemory';

/** Who the revision is attributed to when an operator makes it. */
const ATTRIBUTION = 'you';

export interface ReviseFactProps {
  path: string;
  fact: string;
  /** Index within the list, used for the test id. */
  index: number;
  /** Label of the opening button. */
  label?: string;
}

export function ReviseFact({ path, fact, index, label = 'Revise' }: ReviseFactProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(fact);
  const revise = useRevisePage();

  function close() {
    setOpen(false);
    setDraft(fact);
    revise.reset();
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    const newFact = draft.trim();
    if (!newFact || newFact === fact) return;
    try {
      await revise.mutateAsync({ path, oldFact: fact, newFact, attribution: ATTRIBUTION });
    } catch {
      // The error is rendered below; the optimistic row is already rolled back.
      return;
    }
    setOpen(false);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary"
        onClick={() => setOpen(true)}
        aria-label={label || `Revise: ${fact}`}
        data-testid={`memory-revise-${index}`}
      >
        <Pencil size={12} aria-hidden="true" />
        {label}
      </button>
    );
  }

  return (
    <form
      className="niuu:flex niuu:w-full niuu:flex-col niuu:gap-2"
      onSubmit={save}
      data-testid={`memory-revise-form-${index}`}
    >
      <textarea
        className="niuu:min-h-16 niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary niuu:outline-none"
        value={draft}
        aria-label={`Revise: ${fact}`}
        onChange={(event) => setDraft(event.target.value)}
      />
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <button
          type="submit"
          className="niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-[11px] niuu:text-brand-300 niuu:disabled:opacity-40"
          disabled={revise.isPending || draft.trim().length === 0 || draft.trim() === fact}
        >
          {revise.isPending ? 'Writing…' : 'Save revision'}
        </button>
        <button
          type="button"
          className="niuu:text-[11px] niuu:text-text-muted niuu:hover:text-text-primary"
          onClick={close}
          disabled={revise.isPending}
        >
          Cancel
        </button>
        <span className="niuu:text-[11px] niuu:text-text-faint">
          the old wording stays in the evidence trail
        </span>
      </div>
      {revise.isError ? (
        <p className="niuu:text-[11px] niuu:text-critical-fg" role="alert">
          {revise.error instanceof Error ? revise.error.message : 'Could not write the revision'}
        </p>
      ) : null}
    </form>
  );
}
