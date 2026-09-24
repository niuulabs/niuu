import { useMemo } from 'react';
import { Plus, Search } from 'lucide-react';
import { cn, relTime } from '@niuulabs/ui';
import type { Ravn } from '../../domain/ravn';
import { nameForRavn, ravnKey } from '../../domain/residentActions';
import {
  RAVN_FILTERS,
  RAVN_GROUPINGS,
  filterCounts,
  fleetUsage,
  formatTokens,
  formatUsd,
  groupRavnsBy,
  matchesFilter,
  matchesQuery,
  ravnLifeState,
  ravnReasonLine,
  type RavnFilter,
  type RavnGrouping,
} from '../../application/ravnWorkbench';
import { EngineLabel, RavnMark, RavnStateBadge } from './RavnMark';

export interface RavnListProps {
  ravens: Ravn[];
  selectedKey: string | null;
  onSelect: (ravn: Ravn) => void;
  filter: RavnFilter;
  onFilterChange: (filter: RavnFilter) => void;
  grouping: RavnGrouping;
  onGroupingChange: (grouping: RavnGrouping) => void;
  query: string;
  onQueryChange: (query: string) => void;
  onDeploy: () => void;
}

function lastSeen(ravn: Ravn): string {
  const state = ravnLifeState(ravn);
  if (state === 'starting') return 'new';
  const at = ravn.updatedAt ?? ravn.createdAt;
  return relTime(at);
}

function RavnRow({
  ravn,
  selected,
  onSelect,
}: {
  ravn: Ravn;
  selected: boolean;
  onSelect: (ravn: Ravn) => void;
}) {
  const state = ravnLifeState(ravn);
  const reason = ravnReasonLine(ravn);
  const progress = state === 'starting' || state === 'removing';
  return (
    <button
      type="button"
      className="rw-row"
      aria-current={selected}
      onClick={() => onSelect(ravn)}
      data-testid={`ravn-row-${ravnKey(ravn)}`}
    >
      <RavnMark ravn={ravn} showState />
      <span className="rw-row__body">
        <span className="rw-row__name">{nameForRavn(ravn)}</span>
        <span className="rw-row__meta">
          <EngineLabel engine={ravn.engine} />
          <span className="rw-dot-sep">·</span>
          <span>{ravn.model}</span>
        </span>
        {reason && (
          <span className="rw-row__why" data-tone={progress ? 'progress' : 'critical'}>
            {reason}
          </span>
        )}
      </span>
      <span className="rw-row__side">
        <RavnStateBadge state={state} />
        <span className="rw-row__age">{lastSeen(ravn)}</span>
      </span>
    </button>
  );
}

export function RavnList({
  ravens,
  selectedKey,
  onSelect,
  filter,
  onFilterChange,
  grouping,
  onGroupingChange,
  query,
  onQueryChange,
  onDeploy,
}: RavnListProps) {
  const counts = useMemo(() => filterCounts(ravens), [ravens]);
  const groups = useMemo(
    () =>
      groupRavnsBy(
        ravens.filter((ravn) => matchesFilter(ravn, filter) && matchesQuery(ravn, query)),
        grouping,
      ),
    [ravens, filter, query, grouping],
  );
  const usage = useMemo(() => fleetUsage(ravens), [ravens]);
  // A pill with nothing behind it is noise; "All" and the active one always show.
  const visibleFilters = RAVN_FILTERS.filter(
    (option) => option.id === 'all' || counts[option.id] > 0 || filter === option.id,
  );

  return (
    <aside className="rw-rail" aria-label="Ravens" data-testid="ravn-list">
      <div className="rw-rail__head">
        <div>
          <h2 className="rw-rail__title">Ravens</h2>
          <div className="rw-rail__sub">
            {counts.running} running
            {counts.attention > 0 && ` · ${counts.attention} need attention`}
          </div>
        </div>
        <button
          type="button"
          className="rw-btn rw-btn--primary"
          onClick={onDeploy}
          data-testid="ravn-deploy-open"
        >
          <Plus size={14} aria-hidden="true" />
          Deploy
        </button>
      </div>

      <div className="rw-pills" role="group" aria-label="Filter by state">
        {visibleFilters.map((option) => (
          <button
            key={option.id}
            type="button"
            className={cn('rw-pill', option.id === 'attention' && 'rw-pill--attention')}
            aria-pressed={filter === option.id}
            onClick={() => onFilterChange(option.id)}
            data-testid={`ravn-filter-${option.id}`}
          >
            {option.label}
            <span className="rw-pill__n">{counts[option.id]}</span>
          </button>
        ))}
      </div>

      <div className="rw-tools">
        <label className="rw-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Filter by name, persona, model…"
            aria-label="Filter ravens"
            data-testid="ravn-search"
          />
        </label>
        <label className="rw-group-select">
          Group
          <select
            value={grouping}
            onChange={(event) => onGroupingChange(event.target.value as RavnGrouping)}
            aria-label="Group ravens by"
            data-testid="ravn-grouping"
          >
            {RAVN_GROUPINGS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="rw-rail__scroll">
        {groups.length === 0 ? (
          <p className="rw-rail__empty" data-testid="ravn-list-empty">
            {ravens.length === 0
              ? 'No ravens yet. Deploy one to get started.'
              : 'No ravens match these filters.'}
          </p>
        ) : (
          groups.map((group) => (
            <section key={group.key || 'none'} aria-label={group.label}>
              <div className="rw-group-head">
                <span>{group.label}</span>
                <span>{group.ravens.length}</span>
              </div>
              {group.ravens.map((ravn) => (
                <RavnRow
                  key={ravnKey(ravn)}
                  ravn={ravn}
                  selected={ravnKey(ravn) === selectedKey}
                  onSelect={onSelect}
                />
              ))}
            </section>
          ))
        )}
      </div>

      <div className="rw-rail__foot" data-testid="ravn-fleet-usage">
        <div>
          <span className="rw-rail__foot-k">Fleet, since deploy</span> ·{' '}
          {usage.messages.toLocaleString()} messages · {formatTokens(usage.tokens)} tokens ·{' '}
          {formatUsd(usage.costUsd)}
        </div>
      </div>
    </aside>
  );
}
