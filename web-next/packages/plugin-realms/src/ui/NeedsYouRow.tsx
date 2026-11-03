import { Link } from '@tanstack/react-router';
import {
  isOperatorQuestion,
  reviewKindLabel,
  ReviewDecisionPanel,
  type ReviewItem,
} from '@niuulabs/plugin-valkyrie';
import { Chip } from '@niuulabs/ui';
import type { RealmHomeCard } from '../application/useRealmsHome';
import { TemplateIcon } from './icons';

const LINK = 'niuu:shrink-0 niuu:text-[11px] niuu:text-brand-300';

/**
 * One review waiting on the operator. Residents mostly ask questions, so the
 * question is shown whole and answered here, the same way the inbox answers it.
 */
export function NeedsYouRow({
  item,
  card,
  testId,
}: {
  item: ReviewItem;
  card: RealmHomeCard | null;
  testId?: string;
}) {
  const question = isOperatorQuestion(item);
  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-2 niuu:border-b niuu:border-border-subtle niuu:py-3 niuu:last:border-b-0"
      data-testid={testId ?? `needs-you-${item.itemId}`}
    >
      <div className="niuu:flex niuu:items-start niuu:gap-3">
        <span className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand/40 niuu:bg-brand/10 niuu:text-brand">
          <TemplateIcon templateId={card?.binding?.template} size={13} />
        </span>
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-0.5">
          <p className="niuu:m-0 niuu:text-sm niuu:text-text-primary">{item.title}</p>
          <span className="niuu:truncate niuu:text-xs niuu:text-text-muted">
            {card ? `${card.realm.name} · ` : ''}
            {item.summary}
          </span>
        </div>
        <Chip tone="muted">{question ? 'Question' : reviewKindLabel(item.kind)}</Chip>
        {card ? (
          <Link
            to="/realms/$slug"
            params={{ slug: card.realm.slug }}
            className={LINK}
            title={`Open ${card.realm.name}`}
          >
            Open
          </Link>
        ) : (
          <Link to={'/valkyrie/inbox' as never} className={LINK} title="Open in the inbox">
            Open
          </Link>
        )}
      </div>
      <div className="niuu:pl-10">
        <ReviewDecisionPanel item={item} compact />
      </div>
    </div>
  );
}
