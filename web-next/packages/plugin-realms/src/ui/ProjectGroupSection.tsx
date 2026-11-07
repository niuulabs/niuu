import { useId } from 'react';
import { Link } from '@tanstack/react-router';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { ProjectIcon } from '@niuulabs/ui';
import { needsYouCount, type RealmHomeCard } from '../application/useRealmsHome';
import type { ProjectGroup } from '../domain/projects';
import { RealmCard } from './RealmCard';

const GROUP_TITLE =
  'niuu:m-0 niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5 niuu:text-sm niuu:font-semibold niuu:text-text-primary';

/** One project's workspaces. Folded away, its header still says what is going on inside. */
export function ProjectGroupSection({
  group,
  collapsed,
  onToggle,
}: {
  group: ProjectGroup<RealmHomeCard>;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const bodyId = useId();
  const only = group.cards.length === 1 ? group.cards[0] : null;
  const needsYou = group.cards.reduce((sum, card) => sum + needsYouCount(card), 0);
  const inSessions = group.cards.reduce((sum, card) => sum + card.runningSessions, 0);
  const Chevron = collapsed ? ChevronRight : ChevronDown;

  return (
    <section
      className="niuu:flex niuu:flex-col niuu:gap-2"
      aria-label={group.name}
      data-testid={`project-group-${group.id ?? 'none'}`}
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
        <div className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1">
          <button
            type="button"
            className="niuu:flex niuu:shrink-0 niuu:rounded niuu:p-1 niuu:text-text-muted niuu:hover:text-text-primary"
            aria-expanded={!collapsed}
            aria-controls={collapsed ? undefined : bodyId}
            aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${group.name}`}
            onClick={onToggle}
          >
            <Chevron size={14} aria-hidden="true" />
          </button>
          {group.id === null ? (
            <h2 className={GROUP_TITLE}>{group.name}</h2>
          ) : (
            <h2 className={GROUP_TITLE}>
              <ProjectIcon size={14} className="niuu:text-brand-300" />
              {only ? (
                <Link to="/realms/$slug" params={{ slug: only.realm.slug }}>
                  {group.name}
                </Link>
              ) : (
                <Link
                  to={'/realms/projects/$projectId' as never}
                  params={{ projectId: group.id } as never}
                >
                  {group.name}
                </Link>
              )}
            </h2>
          )}
        </div>
        <span className="niuu:flex niuu:shrink-0 niuu:gap-3 niuu:text-xs niuu:text-text-muted">
          {collapsed && needsYou > 0 ? (
            <span className="niuu:text-status-amber">{needsYou} needs you</span>
          ) : null}
          {collapsed && inSessions > 0 ? <span>{inSessions} in sessions</span> : null}
          <span>
            {group.cards.length} workspace{group.cards.length === 1 ? '' : 's'}
          </span>
        </span>
      </div>
      {collapsed ? null : (
        <div id={bodyId} className="niuu:grid niuu:grid-cols-3 niuu:gap-4">
          {group.cards.map((card) => (
            <RealmCard
              key={card.realm.slug}
              realm={card.realm}
              binding={card.binding}
              boardName={card.boardName}
              waitingTickets={card.waitingTickets.length}
              runningSessions={card.runningSessions}
            />
          ))}
        </div>
      )}
    </section>
  );
}
