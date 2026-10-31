import './TicketReviewPage.css';
import { Link, useParams } from '@tanstack/react-router';
import { ExternalLink } from 'lucide-react';
import type { WorkBoardLayout, WorkTask } from '../../domain/work';
import type { RunReview } from '../../domain/review';
import { CoordinatorChat } from '../CoordinatorChat';
import { useGateDecision, useTicketReviews, useWorkDetail } from '../useWork';
import { ReviewWorkspace } from './ReviewWorkspace';

function TicketReview({
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
  const decide = useGateDecision(projectId);
  return (
    <ReviewWorkspace
      subject={{
        identifier: task.source.identifier,
        title: task.title,
        gate: task.board?.gate ?? null,
        pullRequestUrl: task.workflowRun?.prUrl ?? task.operationalRun?.prUrl ?? null,
        canAsk: task.coordinator?.status === 'active' && Boolean(task.coordinator.chatEndpoint),
      }}
      board={board}
      reviews={reviews}
      railLabel="Ticket"
      rail={
        <>
          {task.description ? <p>{task.description}</p> : null}
          <a href={task.source.url} target="_blank" rel="noreferrer">
            Open ticket <ExternalLink aria-hidden="true" />
          </a>
          {planSlug ? (
            <Link to={`/ting/plan/${planSlug}` as never}>Open the project plan</Link>
          ) : null}
        </>
      }
      talk={(talk) => (
        <CoordinatorChat coordinator={task.coordinator} name={task.source.identifier} {...talk} />
      )}
      deciding={decide.isPending}
      decisionFailure={decide.error}
      onDecide={(decision, onSuccess) =>
        decide.mutate({ issueId: task.source.issueId, decision }, { onSuccess })
      }
    />
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
        <TicketReview
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
