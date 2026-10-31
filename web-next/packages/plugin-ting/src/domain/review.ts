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
  /** The project plan this ticket's project was committed from. */
  planSlug?: string | null;
}

export type ReviewState = 'waiting' | 'running' | 'done' | 'failed';

/** One run a person reviews, as the Reviews list shows it across projects. */
export interface ReviewItem {
  taskId: string;
  kind: 'ticket' | 'projectPlan';
  title: string;
  workflowName: string;
  state: ReviewState;
  gateLabel: string;
  updatedAt: string;
  projectId?: string | null;
  projectName?: string | null;
  issueId?: string | null;
  identifier?: string | null;
}

export interface ReviewList {
  items: ReviewItem[];
}

/** Where a review lives: a ticket's review page, or its project plan's. */
export function reviewPath(item: ReviewItem): string {
  return item.kind === 'ticket' && item.projectId && item.issueId
    ? `/ting/review/${item.projectId}/${item.issueId}`
    : `/ting/plan/${item.taskId}`;
}

/** A quote shorter than this would mark text that merely happens to match. */
const MIN_QUOTE_LENGTH = 4;

/** The passages a piece of text quotes, one per Markdown quote line. */
export function quotesIn(text: string): string[] {
  return text
    .split('\n')
    .filter((line) => line.startsWith('> '))
    .map((line) => line.slice(2).trim())
    .filter((line) => line.length >= MIN_QUOTE_LENGTH);
}

/** What a person has quoted to the coordinator, from the chat they already share. */
export function quotesFromMessages(
  messages: readonly { role: string; content: string }[],
): string[] {
  const quotes = messages
    .filter((message) => message.role === 'user')
    .flatMap((message) => quotesIn(message.content));
  return [...new Set(quotes)];
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
