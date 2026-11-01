import { Link } from '@tanstack/react-router';
import { reviewPath, type ReviewItem } from '@niuulabs/plugin-ting';
import { Chip } from '@niuulabs/ui';

const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300';

/** A ticket at a gate. The decision is made on its review, where the work can be read. */
export function TicketReviewRow({ item, workspace }: { item: ReviewItem; workspace: string }) {
  const label = [workspace, item.identifier, item.gateLabel].filter(Boolean).join(' · ');
  return (
    <div
      className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:last:border-b-0"
      data-testid={`needs-you-ticket-${item.taskId}`}
    >
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{item.title}</span>
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">{label}</span>
      </div>
      <Chip tone="muted">{item.workflowName}</Chip>
      <Link to={reviewPath(item) as never} className={PRIMARY}>
        Review
      </Link>
    </div>
  );
}

/** Says so when what is waiting could not be read, so an empty list is never a guess. */
export function NeedsYouError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <span className="niuu:py-1 niuu:text-xs niuu:text-critical-fg" data-testid="needs-you-error">
      Could not read everything waiting on you: {String(error)}
    </span>
  );
}
