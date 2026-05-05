import { useEffect, useMemo, useState } from 'react';
import { BudgetBar, PersonaAvatar, StateDot, cn, ErrorState, LoadingState } from '@niuulabs/ui';
import type { BudgetState, PersonaRole } from '@niuulabs/domain';
import type { Ravn } from '../domain/ravn';
import { useRavens } from './hooks/useRavens';
import { useRavnBudgets } from './hooks/useBudget';
import { useSessions } from './hooks/useSessions';
import { groupRavens, ravnStatusToDotState, type GroupKey } from './grouping';
import { RavnDetail } from './RavnDetail';
import { loadStorage, saveStorage } from './storage';
import './RavensPage.css';

const GROUP_STORAGE_KEY = 'ravn.ravens.group';

const GROUP_OPTIONS: Array<{ key: GroupKey; label: string }> = [
  { key: 'location', label: 'loc' },
  { key: 'persona', label: 'persona' },
  { key: 'state', label: 'state' },
  { key: 'none', label: 'flat' },
];

const ROLE_LABELS: Partial<Record<PersonaRole, string>> = {
  arbiter: 'arbiter',
  audit: 'auditor',
  autonomy: 'autonomous',
  build: 'coder',
  coord: 'coordinator',
  gate: 'gatekeeper',
  index: 'indexer',
  investigate: 'investigator',
  knowledge: 'curator',
  observe: 'observer',
  plan: 'planner',
  qa: 'tester',
  report: 'reporter',
  review: 'reviewer',
  ship: 'shipper',
  verify: 'verifier',
  write: 'writer',
};

function normalizeLabel(value: string): string {
  return value.replace(/_/g, ' ').replace(/-/g, ' ');
}

function titleCase(value: string): string {
  return normalizeLabel(value).replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatBudgetText(budget?: BudgetState): string {
  if (!budget) return '—';
  return `$${budget.spentUsd.toFixed(2)}/$${budget.capUsd.toFixed(2)}`;
}

function subtitleForRavn(ravn: Ravn): string {
  return ROLE_LABELS[ravn.role ?? 'build'] ?? ravn.role ?? 'raven';
}

function matchesQuery(ravn: Ravn, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;

  const fields = [
    ravn.personaName,
    ravn.role,
    ravn.location,
    ravn.deployment,
    ravn.summary,
    ravn.id,
  ];

  return fields.some((value) => value?.toLowerCase().includes(needle));
}

function pickDefaultRavn(ravens: Ravn[]): string | null {
  if (ravens.length === 0) return null;
  return ravens.find((ravn) => ravn.status === 'active')?.id ?? ravens[0]!.id;
}

interface RavnListRowProps {
  ravn: Ravn;
  budget?: BudgetState;
  sessionCount: number;
  selected: boolean;
  onClick: () => void;
}

function RavnListRow({ ravn, budget, sessionCount, selected, onClick }: RavnListRowProps) {
  const letter = ravn.letter ?? ravn.personaName.charAt(0).toUpperCase();
  const role = ravn.role ?? 'build';

  return (
    <button
      type="button"
      onClick={onClick}
      data-testid="ravn-list-row"
      aria-selected={selected}
      className={cn('rv-list-row', selected && 'rv-list-row--selected')}
    >
      <span className="rv-list-row__state">
        <StateDot
          state={ravnStatusToDotState(ravn.status)}
          pulse={ravn.status === 'active'}
          size={9}
        />
      </span>

      <span className="rv-list-row__avatar" aria-hidden="true">
        <PersonaAvatar role={role} letter={letter} size={28} />
      </span>

      <span className="rv-list-row__identity">
        <span className="rv-list-row__name">{ravn.personaName}</span>
        <span className="rv-list-row__sub">{subtitleForRavn(ravn)}</span>
      </span>

      <span className="rv-list-row__location">
        <span>{normalizeLabel(ravn.location ?? 'unknown')}</span>
        <span>{normalizeLabel(ravn.deployment ?? 'unplaced')}</span>
      </span>

      <span className="rv-list-row__sessions">
        <span className="rv-list-row__sessions-value">{sessionCount}</span>
        <span className="rv-list-row__sessions-label">sess</span>
      </span>

      <span className="rv-list-row__budget">
        {budget ? (
          <>
            <BudgetBar
              spent={budget.spentUsd}
              cap={budget.capUsd}
              warnAt={Math.round(budget.warnAt * 100)}
              size="sm"
            />
            <span className="rv-list-row__budget-text">{formatBudgetText(budget)}</span>
          </>
        ) : (
          <span className="rv-list-row__budget-text">—</span>
        )}
      </span>
    </button>
  );
}

interface FleetGroupProps {
  label: string;
  count: number;
}

function FleetGroupHeader({ label, count }: FleetGroupProps) {
  return (
    <div className="rv-group-header">
      <span className="rv-group-header__label">{label}</span>
      <span className="rv-group-header__count">{count}</span>
    </div>
  );
}

export function RavensPage() {
  const [groupBy, setGroupBy] = useState<GroupKey>(() =>
    loadStorage<GroupKey>(GROUP_STORAGE_KEY, 'location'),
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedRavnId, setSelectedRavnId] = useState<string | null>(null);

  const { data: ravens, isLoading, isError, error } = useRavens();
  const { data: sessions } = useSessions();

  const ravnList = useMemo(() => ravens ?? [], [ravens]);
  const budgets = useRavnBudgets(ravnList.map((ravn) => ravn.id));

  const sessionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const session of sessions ?? []) {
      counts.set(session.ravnId, (counts.get(session.ravnId) ?? 0) + 1);
    }
    return counts;
  }, [sessions]);

  useEffect(() => {
    if (ravnList.length === 0) {
      setSelectedRavnId(null);
      return;
    }

    if (!selectedRavnId || !ravnList.some((ravn) => ravn.id === selectedRavnId)) {
      setSelectedRavnId(pickDefaultRavn(ravnList));
    }
  }, [ravnList, selectedRavnId]);

  const filteredRavens = useMemo(
    () => ravnList.filter((ravn) => matchesQuery(ravn, searchQuery)),
    [ravnList, searchQuery],
  );

  const groupedEntries = useMemo(() => {
    const entries = Object.entries(groupRavens(filteredRavens, groupBy));
    if (groupBy === 'none') return entries;
    return entries.sort(([left], [right]) => left.localeCompare(right));
  }, [filteredRavens, groupBy]);

  const selectedRavn =
    ravnList.find((ravn) => ravn.id === selectedRavnId) ?? filteredRavens[0] ?? ravnList[0] ?? null;

  const activeCount = ravnList.filter((ravn) => ravn.status === 'active').length;
  const failedCount = ravnList.filter((ravn) => ravn.status === 'failed').length;

  if (isLoading) {
    return (
      <div data-testid="ravens-loading">
        <LoadingState label="Loading ravens…" />
      </div>
    );
  }

  if (isError) {
    return (
      <div data-testid="ravens-error">
        <ErrorState message={error instanceof Error ? error.message : 'Failed to load ravens'} />
      </div>
    );
  }

  return (
    <div data-testid="ravens-page" className="rv-ravens">
      <div className="rv-ravens__content">
        <aside
          className={cn('rv-fleet', sidebarCollapsed && 'rv-fleet--collapsed')}
          aria-label="Fleet directory"
          data-testid="ravens-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="rv-fleet__collapsed">
              <div className="rv-fleet__collapsed-head">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(false)}
                  className="rv-fleet__toggle"
                  data-testid="ravens-sidebar-toggle"
                  aria-label="Expand ravens sidebar"
                >
                  ›
                </button>
              </div>

              <div className="rv-fleet__collapsed-body">
                {groupedEntries.map(([groupLabel, groupRavns]) => (
                  <div key={groupLabel} className="rv-fleet__collapsed-group">
                    {groupRavns.map((ravn) => (
                      <button
                        key={ravn.id}
                        type="button"
                        onClick={() => setSelectedRavnId(ravn.id)}
                        className={cn(
                          'rv-fleet__collapsed-item',
                          selectedRavn?.id === ravn.id && 'rv-fleet__collapsed-item--selected',
                        )}
                        aria-label={ravn.personaName}
                      >
                        <StateDot
                          state={ravnStatusToDotState(ravn.status)}
                          pulse={ravn.status === 'active'}
                          size={9}
                        />
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rv-fleet__expanded">
              <div className="rv-fleet__head">
                <div className="rv-fleet__title-row">
                  <div>
                    <h2 className="rv-fleet__title">Fleet</h2>
                    <div className="rv-fleet__counts" data-testid="fleet-counts">
                      <span>{ravnList.length} total</span>
                      <span className="rv-fleet__sep">·</span>
                      <span className="rv-fleet__counts--active">{activeCount} active</span>
                      {failedCount > 0 && (
                        <>
                          <span className="rv-fleet__sep">·</span>
                          <span className="rv-fleet__counts--failed">{failedCount} failed</span>
                        </>
                      )}
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => setSidebarCollapsed(true)}
                    className="rv-fleet__toggle"
                    data-testid="ravens-sidebar-toggle"
                    aria-label="Collapse ravens sidebar"
                  >
                    ‹
                  </button>
                </div>

                <div className="rv-fleet__controls">
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    className="rv-fleet__search"
                    placeholder="filter by name, persona, location…"
                    aria-label="Filter ravens"
                    data-testid="ravens-search"
                  />

                  <div
                    role="group"
                    aria-label="Group ravens"
                    className="rv-fleet__groupseg"
                    data-testid="grouping-selector"
                  >
                    {GROUP_OPTIONS.map((option) => (
                      <button
                        key={option.key}
                        type="button"
                        onClick={() => {
                          setGroupBy(option.key);
                          saveStorage(GROUP_STORAGE_KEY, option.key);
                        }}
                        className={cn(
                          'rv-fleet__groupbtn',
                          groupBy === option.key && 'rv-fleet__groupbtn--active',
                        )}
                        aria-pressed={groupBy === option.key}
                        data-testid={`group-btn-${option.key}`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <div className="rv-fleet__body" data-testid="layout-split">
                {filteredRavens.length === 0 ? (
                  <div className="rv-fleet__empty">no ravens match &quot;{searchQuery}&quot;</div>
                ) : (
                  groupedEntries.map(([groupLabel, groupRavns]) => (
                    <section key={groupLabel} className="rv-fleet__section">
                      {groupBy !== 'none' && (
                        <FleetGroupHeader label={titleCase(groupLabel)} count={groupRavns.length} />
                      )}

                      <div className="rv-fleet__rows">
                        {groupRavns.map((ravn) => (
                          <RavnListRow
                            key={ravn.id}
                            ravn={ravn}
                            budget={budgets[ravn.id]}
                            sessionCount={sessionCounts.get(ravn.id) ?? 0}
                            selected={selectedRavn?.id === ravn.id}
                            onClick={() => setSelectedRavnId(ravn.id)}
                          />
                        ))}
                      </div>
                    </section>
                  ))
                )}
              </div>
            </div>
          )}
        </aside>

        <section className="rv-ravens__detail">
          {selectedRavn ? (
            <RavnDetail ravn={selectedRavn} />
          ) : (
            <div className="rv-detail-empty" data-testid="detail-empty">
              No ravn available
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
