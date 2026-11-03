import './TicketReviewPage.css';
import { Link } from '@tanstack/react-router';
import { reviewPath, type ReviewItem, type ReviewState } from '../../domain/review';
import { useReviews } from '../useWork';

const GROUPS: readonly { state: ReviewState; title: string }[] = [
  { state: 'waiting', title: 'Waiting for you' },
  { state: 'running', title: 'In progress' },
  { state: 'done', title: 'Decided' },
  { state: 'failed', title: 'Stopped' },
];

function ReviewRow({ item }: { item: ReviewItem }) {
  return (
    <Link className="ting-reviews__row" to={reviewPath(item) as never}>
      <span>
        <span className="ting-reviews__title">
          {item.identifier ? `${item.identifier} · ` : ''}
          {item.title}
        </span>
        <small>
          {item.kind === 'projectPlan' ? 'Project plan' : item.workflowName}
          {item.projectName ? ` · ${item.projectName}` : ''}
          {item.startedBy ? ` · started by ${item.startedBy}` : ''}
        </small>
      </span>
      <span>{item.gateLabel}</span>
      <span>{new Date(item.updatedAt).toLocaleDateString()}</span>
    </Link>
  );
}

/** What is waiting on a person across projects, and what they have already decided. */
export function ReviewsPage() {
  const reviews = useReviews();
  const items = reviews.data?.items ?? [];
  return (
    <div className="ting-reviews">
      <header className="ting-reviews__head">
        <h1>Reviews</h1>
        <Link to={'/ting/plan/new' as never}>Plan a project</Link>
      </header>
      {reviews.isLoading ? <p>Loading reviews…</p> : null}
      {reviews.error ? (
        <p role="alert">
          {reviews.error instanceof Error ? reviews.error.message : 'Reviews could not be read.'}
        </p>
      ) : null}
      {!reviews.isLoading && !reviews.error && items.length === 0 ? (
        <p>Nothing to review yet.</p>
      ) : null}
      {GROUPS.map(({ state, title }) => {
        const inGroup = items.filter((item) => item.state === state);
        if (inGroup.length === 0) return null;
        return (
          <section key={state} className="ting-reviews__group" aria-label={title}>
            <h2>
              {title} · {inGroup.length}
            </h2>
            {inGroup.map((item) => (
              <ReviewRow key={item.taskId} item={item} />
            ))}
          </section>
        );
      })}
    </div>
  );
}
