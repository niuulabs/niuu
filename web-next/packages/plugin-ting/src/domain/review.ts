import type { ReviewPanel } from './work';

/** One version of the work, as a person saw it at one gate. */
export interface ReviewRevision {
  number: number;
  gateId: string;
  activationId: string;
  requestedAt: string;
  status: string;
  summary: string;
  decidedBy: string;
  notes: string;
  decidedAt: string;
  fields: Record<string, string>;
}

/** Everything one gate column of one run has put in front of a person. */
export interface RunReview {
  columnId: string;
  title: string;
  review: string | null;
  workflowName: string;
  taskId: string;
  sessionId: string;
  panels: ReviewPanel[];
  revisions: ReviewRevision[];
  /** The run's session while it is up; diffs are read from it. */
  chatEndpoint?: string | null;
}

export interface TicketReviews {
  reviews: RunReview[];
  /** Runs whose session is gone, so their reviews cannot be read back. */
  unreadableRuns: string[];
}

export const REVISION_PENDING = 'pending';

export function isPending(revision: ReviewRevision): boolean {
  return revision.status === REVISION_PENDING;
}

/** The review to open first: the one waiting for a person, or else the newest. */
export function openingReview(reviews: readonly RunReview[]): RunReview | null {
  return (
    reviews.find((review) => review.revisions.some(isPending)) ??
    reviews[reviews.length - 1] ??
    null
  );
}

function quoted(text: string): string {
  return text
    .trim()
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n');
}

/** Quote a passage into the person's note, leaving what they already wrote alone. */
export function addQuoteToNote(note: string, quote: string): string {
  const block = `${quoted(quote)}\n`;
  return note.trim() ? `${note.trimEnd()}\n\n${block}` : block;
}

export function questionAboutQuote(quote: string, question: string): string {
  return `${quoted(quote)}\n\n${question.trim()}`;
}

export type DiffLine = { kind: 'same' | 'added' | 'removed'; text: string };

/** A line diff between two revisions, by longest common subsequence. */
export function diffLines(before: string, after: string): DiffLine[] {
  const old = before.split('\n');
  const next = after.split('\n');
  const rows = old.length + 1;
  const columns = next.length + 1;
  const lengths = new Array<number>(rows * columns).fill(0);
  const at = (row: number, column: number) => lengths[row * columns + column] ?? 0;
  for (let row = old.length - 1; row >= 0; row -= 1) {
    for (let column = next.length - 1; column >= 0; column -= 1) {
      lengths[row * columns + column] =
        old[row] === next[column]
          ? at(row + 1, column + 1) + 1
          : Math.max(at(row + 1, column), at(row, column + 1));
    }
  }
  const lines: DiffLine[] = [];
  let row = 0;
  let column = 0;
  while (row < old.length || column < next.length) {
    const removed = old[row];
    const added = next[column];
    if (removed !== undefined && removed === added) {
      lines.push({ kind: 'same', text: removed });
      row += 1;
      column += 1;
    } else if (
      added !== undefined &&
      (removed === undefined || at(row, column + 1) > at(row + 1, column))
    ) {
      lines.push({ kind: 'added', text: added });
      column += 1;
    } else {
      lines.push({ kind: 'removed', text: removed ?? '' });
      row += 1;
    }
  }
  return lines;
}
