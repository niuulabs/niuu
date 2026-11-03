import './TicketReviewPage.css';
import { Link, useParams } from '@tanstack/react-router';
import { isPending, type RunReview, type RunReviews } from '../../domain/review';
import { useRunGateDecision, useRunReviews } from '../useWork';
import { ReviewWorkspace } from './ReviewWorkspace';

/** A run has no board to ask, so its waiting gate is the one its log shows as pending. */
function pendingGate(reviews: readonly RunReview[]) {
  const revision = reviews.flatMap((review) => review.revisions).find(isPending);
  return revision ? { id: revision.gateId, activationId: revision.activationId } : null;
}

function RunWorkspace({ record }: { record: RunReviews }) {
  const { run, reviews } = record;
  const decide = useRunGateDecision(run.slug);
  return (
    <ReviewWorkspace
      subject={{
        identifier: null,
        title: run.title,
        gate: pendingGate(reviews),
        pullRequestUrl: null,
        canAsk: false,
      }}
      board={null}
      reviews={reviews}
      railLabel="Run"
      rail={
        <>
          {run.prompt ? <p>{run.prompt}</p> : null}
          {run.startedBy ? <span>Started by {run.startedBy}</span> : null}
          {run.branch ? <span>On {run.branch}</span> : null}
          <Link to={`/volundr/sessions/${run.sessionId}` as never}>Open the session</Link>
        </>
      }
      deciding={decide.isPending}
      decisionFailure={decide.error}
      onDecide={(decision, onSuccess) => decide.mutate(decision, { onSuccess })}
    />
  );
}

/** One run's reviews, for a run that belongs to no ticket: a resident asked for it directly. */
export function RunReviewPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  const record = useRunReviews(slug);

  if (record.isLoading) return <p className="ting-review__state">Loading review…</p>;
  if (record.error || !record.data) {
    return (
      <p className="ting-review__state" role="alert">
        {record.error instanceof Error ? record.error.message : 'This review could not be read.'}
      </p>
    );
  }
  const { reviews, unreadableRuns, run } = record.data;
  return (
    <div className="ting-review-page">
      {unreadableRuns.length > 0 ? (
        <p className="ting-review__error" role="alert">
          The session of {unreadableRuns.join(', ')} is gone, so its review cannot be read.
        </p>
      ) : null}
      {reviews.length > 0 ? (
        <RunWorkspace record={record.data} />
      ) : (
        <p className="ting-review__state">
          Nothing to review on {run.workflowName} yet.{' '}
          <Link to={'/ting/plan' as never}>Back to Reviews</Link>
        </p>
      )}
    </div>
  );
}
