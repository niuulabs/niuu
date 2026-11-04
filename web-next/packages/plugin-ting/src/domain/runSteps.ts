import { isPending, type RunReview } from './review';
import type { BoardPlacement, WorkBoardLayout } from './work';
import { BOARD_STATE_LABEL } from './workBoard';

export type RunStepState = 'done' | 'current' | 'waiting' | 'ahead';

/** One step of a run as its page shows it, from what the board or the run's gates record. */
export interface RunStep {
  id: string;
  title: string;
  state: RunStepState;
  /** What is happening at the step, when the record says. */
  note: string | null;
}

const DECIDED_NOTE: Record<string, string> = {
  approved: 'Approved',
  changes_requested: 'Changes requested',
};

/**
 * A ticket's run: the columns its workflow spans on the board, read against where the ticket
 * sits. Columns before it are behind the ticket; the board never moves a ticket past a column
 * its run has not left.
 */
export function boardSteps(
  board: WorkBoardLayout,
  workflowId: string,
  placement: BoardPlacement | null | undefined,
): RunStep[] {
  const columns = board.columns.filter((column) => column.workflowId === workflowId);
  const at = columns.findIndex((column) => column.id === placement?.columnId);
  const finished = placement?.passedWorkflowIds?.includes(workflowId) ?? false;
  return columns.map((column, index) => {
    if (at === -1) {
      return { id: column.id, title: column.title, state: finished ? 'done' : 'ahead', note: null };
    }
    if (index < at) return { id: column.id, title: column.title, state: 'done', note: null };
    if (index > at) return { id: column.id, title: column.title, state: 'ahead', note: null };
    return {
      id: column.id,
      title: column.title,
      state: placement?.gate ? 'waiting' : 'current',
      note: placement ? BOARD_STATE_LABEL[placement.state] : null,
    };
  });
}

/** A run on no board: the gates it has reached in the order it reached them, as each was left. */
export function gateSteps(reviews: readonly RunReview[]): RunStep[] {
  const reached = reviews
    .filter((review) => review.revisions.length > 0)
    .sort((a, b) => a.revisions[0]!.requestedAt.localeCompare(b.revisions[0]!.requestedAt));
  return reached.map((review) => {
    const latest = review.revisions[review.revisions.length - 1]!;
    if (isPending(latest)) {
      return { id: review.columnId, title: review.title, state: 'waiting', note: null };
    }
    return {
      id: review.columnId,
      title: review.title,
      state: 'done',
      note: DECIDED_NOTE[latest.status] ?? latest.status,
    };
  });
}
