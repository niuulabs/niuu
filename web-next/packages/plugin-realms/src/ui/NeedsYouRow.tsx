import { Link } from '@tanstack/react-router';
import { reviewKindLabel, useDecideReview, type ReviewItem } from '@niuulabs/plugin-valkyrie';
import { Chip } from '@niuulabs/ui';
import type { RealmHomeCard } from '../application/useRealmsHome';
import { TemplateIcon } from './icons';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary';
const PRIMARY =
  'niuu:rounded-md niuu:border niuu:border-brand/50 niuu:bg-brand/10 niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-brand-300';

export function NeedsYouRow({ item, card }: { item: ReviewItem; card: RealmHomeCard | null }) {
  const decide = useDecideReview();
  const busy = decide.isPending;
  return (
    <div
      className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5"
      data-testid={`needs-you-${item.itemId}`}
    >
      <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
        <TemplateIcon templateId={card?.binding?.template} size={13} />
      </span>
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        {card ? (
          <Link
            to="/realms/$slug"
            params={{ slug: card.realm.slug }}
            className="niuu:truncate niuu:text-sm niuu:text-text-primary"
            title={`Open ${card.realm.name}`}
          >
            {item.title}
          </Link>
        ) : (
          <Link
            to={'/valkyrie/inbox' as never}
            className="niuu:truncate niuu:text-sm niuu:text-text-primary"
            title="Open in the inbox"
          >
            {item.title}
          </Link>
        )}
        <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
          {card ? `${card.realm.name} · ` : ''}
          {item.summary}
        </span>
      </div>
      <Chip tone="muted">{reviewKindLabel(item.kind)}</Chip>
      <button
        type="button"
        className={PRIMARY}
        disabled={busy}
        onClick={() => decide.mutate({ itemId: item.itemId, decision: 'approved' })}
      >
        Approve
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={busy}
        onClick={() => decide.mutate({ itemId: item.itemId, decision: 'rejected' })}
      >
        Reject
      </button>
      {decide.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(decide.error)}</span>
      ) : null}
    </div>
  );
}
