import { useMemo, useState } from 'react';
import {
  Dialog,
  DialogContent,
  PersonaAvatar,
  StateDot,
  cn,
  ErrorState,
  LoadingState,
} from '@niuulabs/ui';
import type { BudgetState, PersonaRole } from '@niuulabs/domain';
import type { Ravn } from '../domain/ravn';
import { useRavens } from './hooks/useRavens';
import { useRavnBudgets } from './hooks/useBudget';
import { useSessions } from './hooks/useSessions';
import { groupRavens, ravnStatusToDotState, type GroupKey } from './grouping';
import { RavnDetail } from './RavnDetail';
import { ResidentDeployDialog } from './ResidentDeployDialog';
import { ResidentFlockDeployDialog } from './ResidentFlockDeployDialog';
import { useDeleteResidentFlock } from './hooks/useResidentControl';
import { Plus, Trash2, Users } from 'lucide-react';
import { loadStorage, saveStorage } from './storage';
import './RavensPage.css';

const GROUP_STORAGE_KEY = 'ravn.ravens.group';

const GROUP_OPTIONS: Array<{ key: GroupKey; label: string }> = [
  { key: 'location', label: 'loc' },
  { key: 'persona', label: 'persona' },
  { key: 'state', label: 'state' },
  { key: 'flock', label: 'mesh' },
  { key: 'none', label: 'flat' },
];

const ROLE_LABELS: Record<PersonaRole, string> = {
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
  if (ravn.kind === 'resident' && ravn.personaName) return normalizeLabel(ravn.personaName);
  return ROLE_LABELS[ravn.role ?? 'build'];
}

function nameForRavn(ravn: Ravn): string {
  return ravn.residentName || ravn.personaName || ravn.id.slice(0, 8);
}

function ravnKey(ravn: Pick<Ravn, 'id' | 'instanceId'>): string {
  return ravn.instanceId ? `${encodeURIComponent(ravn.instanceId)}:${ravn.id}` : ravn.id;
}

function matchesQuery(ravn: Ravn, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;

  const fields = [
    ravn.personaName,
    ravn.residentName,
    ravn.role,
    ravn.location,
    ravn.deployment,
    ravn.summary,
    ravn.id,
    ravn.backend,
    ravn.engine,
    ravn.instanceName,
    ravn.flockId,
    ravn.flockRole,
    ravn.flockPeerId,
  ];

  return fields.some((value) => value?.toLowerCase().includes(needle));
}

function pickDefaultRavn(ravens: Ravn[]): string | null {
  if (ravens.length === 0) return null;
  return ravnKey(ravens.find((ravn) => ravn.status === 'active') ?? ravens[0]!);
}

interface RavnListRowProps {
  ravn: Ravn;
  budget?: BudgetState;
  sessionCount: number;
  selected: boolean;
  onClick: () => void;
}

function RavnListRow({ ravn, budget, sessionCount, selected, onClick }: RavnListRowProps) {
  const letter = ravn.letter ?? nameForRavn(ravn).charAt(0).toUpperCase();
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
        <span className="rv-list-row__name">{nameForRavn(ravn)}</span>
        <span className="rv-list-row__sub">
          {subtitleForRavn(ravn)} · {normalizeLabel(ravn.engine || ravn.deployment || 'ravn')}
        </span>
      </span>

      <span className="rv-list-row__summary">
        <span className="rv-list-row__target">
          {normalizeLabel(ravn.instanceName || ravn.location || 'unknown')}
        </span>
        <span className="rv-list-row__metrics">
          <span>{sessionCount} sess</span>
          <span>{formatBudgetText(budget)}</span>
        </span>
      </span>
    </button>
  );
}

interface FleetGroupProps {
  label: string;
  count: number;
  onDelete?: () => void;
}

function FleetGroupHeader({ label, count, onDelete }: FleetGroupProps) {
  return (
    <div className="rv-group-header">
      <span className="rv-group-header__label">{label}</span>
      <span className="rv-group-header__actions">
        <span className="rv-group-header__count">{count}</span>
        {onDelete && (
          <button
            type="button"
            className="rv-group-header__delete"
            onClick={onDelete}
            aria-label={`Delete ${label}`}
            title={`Delete ${label}`}
            data-testid="flock-delete-open"
          >
            <Trash2 size={13} aria-hidden="true" />
          </button>
        )}
      </span>
    </div>
  );
}

interface FlockDeleteTarget {
  label: string;
  ravens: Ravn[];
}

export function RavensPage() {
  const { data: ravens, isLoading, isError, error } = useRavens();

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

  const ravnList = ravens ?? [];
  return <RavensFleet key={ravnList.length === 0 ? 'empty' : 'populated'} ravens={ravnList} />;
}

function RavensFleet({ ravens }: { ravens: Ravn[] }) {
  const [groupBy, setGroupBy] = useState<GroupKey>(() =>
    loadStorage<GroupKey>(GROUP_STORAGE_KEY, 'location'),
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedRavnId, setSelectedRavnId] = useState<string | null>(() =>
    pickDefaultRavn(ravens),
  );
  const [deployOpen, setDeployOpen] = useState(false);
  const [flockDeployOpen, setFlockDeployOpen] = useState(false);
  const [flockDeleteTarget, setFlockDeleteTarget] = useState<FlockDeleteTarget | null>(null);

  const { data: sessions } = useSessions();
  const deleteFlock = useDeleteResidentFlock();

  const ravnList = useMemo(() => ravens, [ravens]);
  const budgets = useRavnBudgets(ravnList.map((ravn) => ravn.id));
  const resolvedSelectedRavnId =
    ravnList.find((ravn) => ravnKey(ravn) === selectedRavnId) !== undefined
      ? selectedRavnId
      : pickDefaultRavn(ravnList);

  const sessionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const session of sessions ?? []) {
      const key = session.instanceId
        ? `${encodeURIComponent(session.instanceId)}:${session.ravnId}`
        : session.ravnId;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [sessions]);

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
    ravnList.find((ravn) => ravnKey(ravn) === resolvedSelectedRavnId) ??
    filteredRavens[0] ??
    ravnList[0] ??
    null;

  const activeCount = ravnList.filter((ravn) => ravn.status === 'active').length;
  const failedCount = ravnList.filter((ravn) => ravn.status === 'failed').length;

  const removeFlock = async (target: FlockDeleteTarget) => {
    try {
      await deleteFlock.mutateAsync(target.ravens);
    } catch {
      return;
    }
    setFlockDeleteTarget(null);
    setSelectedRavnId(null);
  };

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
                        key={ravnKey(ravn)}
                        type="button"
                        onClick={() => setSelectedRavnId(ravnKey(ravn))}
                        className={cn(
                          'rv-fleet__collapsed-item',
                          selectedRavn &&
                            ravnKey(selectedRavn) === ravnKey(ravn) &&
                            'rv-fleet__collapsed-item--selected',
                        )}
                        aria-label={nameForRavn(ravn)}
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

                  <div className="rv-fleet__title-actions">
                    <button
                      type="button"
                      onClick={() => setFlockDeployOpen(true)}
                      className="rv-fleet__deploy"
                      data-testid="flock-deploy-open"
                    >
                      <Users size={14} aria-hidden="true" />
                      Mesh
                    </button>
                    <button
                      type="button"
                      onClick={() => setDeployOpen(true)}
                      className="rv-fleet__deploy"
                      data-testid="resident-deploy-open"
                    >
                      <Plus size={14} aria-hidden="true" />
                      Deploy
                    </button>
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
                        <FleetGroupHeader
                          label={titleCase(groupLabel)}
                          count={groupRavns.length}
                          onDelete={
                            groupBy === 'flock' && groupRavns.every((ravn) => ravn.flockId)
                              ? () => {
                                  deleteFlock.reset();
                                  setFlockDeleteTarget({
                                    label: titleCase(groupLabel),
                                    ravens: ravnList.filter(
                                      (ravn) => ravn.flockId === groupRavns[0]!.flockId,
                                    ),
                                  });
                                }
                              : undefined
                          }
                        />
                      )}

                      <div className="rv-fleet__rows">
                        {groupRavns.map((ravn) => (
                          <RavnListRow
                            key={ravnKey(ravn)}
                            ravn={ravn}
                            budget={budgets[ravn.id]}
                            sessionCount={sessionCounts.get(ravnKey(ravn)) ?? 0}
                            selected={Boolean(
                              selectedRavn && ravnKey(selectedRavn) === ravnKey(ravn),
                            )}
                            onClick={() => setSelectedRavnId(ravnKey(ravn))}
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
            <RavnDetail
              key={ravnKey(selectedRavn)}
              ravn={selectedRavn}
              onDeleted={() => setSelectedRavnId(null)}
            />
          ) : (
            <div className="rv-detail-empty" data-testid="detail-empty">
              No ravn available
            </div>
          )}
        </section>
      </div>
      <ResidentDeployDialog
        open={deployOpen}
        onOpenChange={setDeployOpen}
        onDeployed={(ravn) => setSelectedRavnId(ravnKey(ravn))}
      />
      <ResidentFlockDeployDialog
        open={flockDeployOpen}
        onOpenChange={setFlockDeployOpen}
        onDeployed={(deployed) => {
          const coordinator = deployed.find((ravn) => ravn.flockRole === 'coordinator');
          setSelectedRavnId(ravnKey(coordinator ?? deployed[0]!));
          setGroupBy('flock');
          saveStorage(GROUP_STORAGE_KEY, 'flock');
        }}
      />
      {flockDeleteTarget && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open && !deleteFlock.isPending) setFlockDeleteTarget(null);
          }}
        >
          <DialogContent
            title="Delete mesh"
            description={`This removes all ${flockDeleteTarget.ravens.length} residents in ${flockDeleteTarget.label} and their backend resources.`}
          >
            {deleteFlock.isError && (
              <div className="rv-form-error" role="alert">
                {deleteFlock.error.message}
              </div>
            )}
            <div className="rv-form-actions">
              <button
                type="button"
                className="rv-action-btn"
                onClick={() => setFlockDeleteTarget(null)}
                disabled={deleteFlock.isPending}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rv-action-btn rv-action-btn--danger"
                onClick={() => void removeFlock(flockDeleteTarget)}
                disabled={deleteFlock.isPending}
                data-testid="flock-delete-confirm"
              >
                {deleteFlock.isPending ? 'Deleting…' : 'Delete mesh'}
              </button>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
