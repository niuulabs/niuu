import './TicketReviewPage.css';
import { Link, Navigate, useParams, useSearch } from '@tanstack/react-router';
import { isPending } from '../../domain/review';
import { useTicketReviews, useWorkDetail } from '../useWork';

/**
 * A ticket's review is its run's review: one page for a run, whichever way it was opened. This
 * route sends a ticket to the run that waits for a person, or else to its newest run.
 */
export function TicketReviewPage() {
  const { projectId, issueId } = useParams({ strict: false }) as {
    projectId: string;
    issueId: string;
  };
  const search = useSearch({ strict: false }) as { returnTo?: string };
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
  const waiting = record.data?.reviews.find((review) => review.revisions.some(isPending));
  const slug = waiting?.taskId ?? task.workflowRun?.taskId;
  if (!slug) {
    return (
      <p className="ting-review__state">
        Nothing to review on {task.source.identifier} yet.{' '}
        <Link to={`/ting/work/project:${projectId}` as never}>Back to the board</Link>
      </p>
    );
  }
  return (
    <Navigate
      to={'/ting/artifacts/$slug' as never}
      params={{ slug } as never}
      search={(search.returnTo ? { returnTo: search.returnTo } : {}) as never}
      replace
    />
  );
}
