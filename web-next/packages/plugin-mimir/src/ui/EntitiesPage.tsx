import { useState } from 'react';
import { Chip, StateDot } from '@niuulabs/ui';
import { useEntities } from '../application/useEntities';
import { ENTITY_KINDS } from '../domain/entity';
import type { EntityKind } from '../domain/entity';

const KIND_ICONS: Record<EntityKind, string> = {
  person: '◇',
  org: '◈',
  concept: '◎',
  project: '▣',
  component: '⬡',
  technology: '⌖',
};

const FILTER_BTN_BASE =
  'niuu-px-3 niuu-py-1 niuu-rounded-sm niuu-font-mono niuu-text-xs niuu-cursor-pointer niuu-border niuu-transition-colors';
const FILTER_BTN_ACTIVE =
  'niuu-bg-bg-tertiary niuu-border-border niuu-text-text-primary niuu-font-medium';
const FILTER_BTN_INACTIVE =
  'niuu-bg-transparent niuu-border-transparent niuu-text-text-muted hover:niuu-text-text-secondary hover:niuu-bg-bg-secondary';

export function EntitiesPage() {
  const [filterKind, setFilterKind] = useState<EntityKind | undefined>(undefined);
  const { entities, grouped, isLoading, isError, error } = useEntities(filterKind);

  const kindsWithEntities = filterKind
    ? [filterKind]
    : ENTITY_KINDS.filter((k) => (grouped[k]?.length ?? 0) > 0);

  return (
    <div className="niuu-p-6 niuu-max-w-4xl">
      <h2 className="niuu-m-0 niuu-mb-5 niuu-text-2xl niuu-font-semibold niuu-text-text-primary">
        Entities
      </h2>

      <div
        className="niuu-flex niuu-flex-wrap niuu-gap-2 niuu-mb-6"
        role="group"
        aria-label="Filter by entity type"
      >
        <button
          className={[
            FILTER_BTN_BASE,
            filterKind == null ? FILTER_BTN_ACTIVE : FILTER_BTN_INACTIVE,
          ].join(' ')}
          onClick={() => setFilterKind(undefined)}
          aria-pressed={filterKind == null}
        >
          All
        </button>
        {ENTITY_KINDS.map((k) => (
          <button
            key={k}
            className={[
              FILTER_BTN_BASE,
              filterKind === k ? FILTER_BTN_ACTIVE : FILTER_BTN_INACTIVE,
            ].join(' ')}
            onClick={() => setFilterKind(filterKind === k ? undefined : k)}
            aria-pressed={filterKind === k}
            data-kind={k}
          >
            {KIND_ICONS[k]} {k}
          </button>
        ))}
      </div>

      {isLoading && (
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <StateDot state="processing" pulse />
          <span className="niuu-text-sm niuu-text-text-secondary">loading entities…</span>
        </div>
      )}

      {isError && (
        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <StateDot state="failed" />
          <span className="niuu-text-sm niuu-text-text-secondary">
            {error instanceof Error ? error.message : 'entities load failed'}
          </span>
        </div>
      )}

      {!isLoading && !isError && entities.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted">No entities found.</p>
      )}

      {kindsWithEntities.map((kind) => {
        const group = grouped[kind] ?? [];
        if (group.length === 0) return null;
        return (
          <section key={kind} className="niuu-mb-8">
            <h3 className="niuu-flex niuu-items-center niuu-gap-2 niuu-m-0 niuu-mb-3 niuu-text-sm niuu-text-text-muted niuu-capitalize niuu-tracking-wider niuu-font-normal">
              {KIND_ICONS[kind]} {kind}
              <Chip tone="muted">{group.length}</Chip>
            </h3>
            <ul
              className="niuu-list-none niuu-p-0 niuu-m-0 niuu-grid niuu-gap-2"
              aria-label={`${kind} entities`}
            >
              {group.map((entity) => (
                <li
                  key={entity.path}
                  className="niuu-py-3 niuu-px-4 niuu-border niuu-border-border-subtle niuu-rounded-md niuu-bg-bg-secondary"
                  data-testid="entity-item"
                >
                  <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-mb-1">
                    <span
                      className="niuu-flex-1 niuu-font-medium niuu-text-sm"
                      data-testid="entity-item-title"
                    >
                      {entity.title}
                    </span>
                    {entity.relationshipCount > 0 && (
                      <Chip tone="muted">{entity.relationshipCount} links</Chip>
                    )}
                  </div>
                  <p className="niuu-text-sm niuu-text-text-secondary niuu-m-0 niuu-mb-1">
                    {entity.summary}
                  </p>
                  <span
                    className="niuu-text-xs niuu-text-text-muted niuu-font-mono"
                    data-testid="entity-item-path"
                  >
                    {entity.path}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
