import { useMemo, useState } from 'react';
import {
  Activity,
  ChevronDown,
  Circle,
  GitBranch,
  Moon,
  Radio,
  Shield,
  Users,
  Zap,
} from 'lucide-react';
import {
  filterRosterEntries,
  groupRosterEntries,
  rosterActivityBars,
  rosterEntries,
  rosterReferenceTime,
  valkyrieLastSeenAt,
  ROSTER_GROUP_MODES,
  ROSTER_GROUP_MODE_LABELS,
  type EnvironmentHealth,
  type EnvironmentKind,
  type RosterEntry,
  type RosterGroupMode,
  type ValkyrieDashboard,
  type ValkyrieResident,
  type WakefulnessState,
} from '../domain';
import { timeAgo } from './reviewFormat';

export function healthClasses(health: EnvironmentHealth | ValkyrieResident['status']): string {
  if (health === 'critical' || health === 'blocked') return 'niuu:text-critical';
  if (health === 'degraded' || health === 'busy') return 'niuu:text-state-warn';
  if (health === 'watch' || health === 'online') return 'niuu:text-brand';
  return 'niuu:text-text-secondary';
}

function healthBarClasses(health: EnvironmentHealth): string {
  if (health === 'critical') return 'niuu:bg-critical';
  if (health === 'degraded') return 'niuu:bg-state-warn';
  if (health === 'watch') return 'niuu:bg-brand';
  return 'niuu:bg-state-ok';
}

function kindIcon(kind: EnvironmentKind | undefined) {
  if (kind === 'kubernetes') return <GitBranch size={13} aria-hidden="true" />;
  if (kind === 'host') return <Activity size={13} aria-hidden="true" />;
  if (kind === 'printer') return <Radio size={13} aria-hidden="true" />;
  return <Shield size={13} aria-hidden="true" />;
}

export function wakefulnessIcon(state: WakefulnessState) {
  if (state === 'dreaming') return <Moon size={13} aria-hidden="true" />;
  if (state === 'wakeful') return <Zap size={13} aria-hidden="true" />;
  if (state === 'sleeping') return <Circle size={13} aria-hidden="true" />;
  return <Radio size={13} aria-hidden="true" />;
}

function wakefulnessClasses(state: WakefulnessState): string {
  if (state === 'sleeping') return 'niuu:text-text-muted';
  return 'niuu:text-brand';
}

function ActivityBars({ entry, bars }: { entry: RosterEntry; bars: number[] }) {
  const health = entry.environment?.health ?? 'healthy';
  return (
    <span
      data-testid={`roster-activity-${entry.valkyrie.id}`}
      aria-hidden="true"
      className="niuu:flex niuu:items-end niuu:gap-[3px]"
    >
      {bars.map((count, index) => (
        <span
          key={index}
          data-lit={count > 0}
          className={`niuu:h-3 niuu:w-1 niuu:rounded-sm ${
            count > 0 ? healthBarClasses(health) : 'niuu:bg-bg-elevated'
          }`}
        />
      ))}
    </span>
  );
}

function RosterRow({
  entry,
  bars,
  selected,
  onSelect,
}: {
  entry: RosterEntry;
  bars: number[];
  selected: boolean;
  onSelect: (valkyrieId: string) => void;
}) {
  const { valkyrie, environment } = entry;
  const health = environment?.health ?? 'healthy';
  const lastSeen = valkyrieLastSeenAt(valkyrie);
  return (
    <button
      type="button"
      data-testid={`roster-item-${valkyrie.id}`}
      onClick={() => onSelect(valkyrie.id)}
      aria-pressed={selected}
      className={`niuu:flex niuu:w-full niuu:items-center niuu:gap-3 niuu:rounded-md niuu:border niuu:border-solid niuu:p-3 niuu:text-left ${
        selected
          ? 'niuu:border-brand niuu:bg-brand/12'
          : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
      }`}
    >
      <span className="niuu:relative niuu:shrink-0">
        <span
          className={`niuu:flex niuu:h-9 niuu:w-9 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-secondary ${healthClasses(
            health,
          )}`}
        >
          ᛒ
        </span>
        <span
          className={`niuu:absolute niuu:-bottom-1 niuu:-left-1 niuu:flex niuu:h-4 niuu:w-4 niuu:items-center niuu:justify-center niuu:rounded-full niuu:bg-bg-secondary ${wakefulnessClasses(
            valkyrie.wakefulness,
          )}`}
        >
          {wakefulnessIcon(valkyrie.wakefulness)}
        </span>
      </span>
      <span className="niuu:min-w-0 niuu:flex-1">
        <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
          {valkyrie.name}
        </span>
        <span className="niuu:mt-0.5 niuu:flex niuu:items-center niuu:gap-1.5 niuu:text-xs">
          <span className={wakefulnessClasses(valkyrie.wakefulness)}>{valkyrie.wakefulness}</span>
          <span aria-hidden="true" className="niuu:text-text-muted">
            ·
          </span>
          <span className={healthClasses(health)}>{health}</span>
        </span>
      </span>
      <span className="niuu:flex niuu:shrink-0 niuu:flex-col niuu:items-end niuu:gap-1.5">
        <ActivityBars entry={entry} bars={bars} />
        <span className="niuu:text-[10px] niuu:text-text-muted">
          {lastSeen ? `${timeAgo(lastSeen)} ago` : ''}
        </span>
      </span>
    </button>
  );
}

export function Roster({
  dashboard,
  selectedId,
  onSelect,
}: {
  dashboard: ValkyrieDashboard;
  selectedId: string;
  onSelect: (valkyrieId: string) => void;
}) {
  const [mode, setMode] = useState<RosterGroupMode>('kind');
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const now = rosterReferenceTime(dashboard);
  const events = dashboard.telemetry?.recentEvents ?? [];

  const groups = useMemo(
    () => groupRosterEntries(filterRosterEntries(rosterEntries(dashboard), query), mode),
    [dashboard, query, mode],
  );

  const toggleGroup = (key: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });

  return (
    <aside
      data-testid="valkyrie-roster"
      className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:border-r niuu:border-border niuu:bg-bg-secondary"
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-3 niuu:border-b niuu:border-border niuu:p-4">
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
          <h2 className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.16em] niuu:text-text-muted">
            Roster
          </h2>
          <select
            aria-label="Group roster by"
            data-testid="roster-group-mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as RosterGroupMode)}
            className="niuu:cursor-pointer niuu:border-0 niuu:bg-transparent niuu:p-0 niuu:text-right niuu:font-mono niuu:text-xs niuu:text-text-muted"
          >
            {ROSTER_GROUP_MODES.map((entry) => (
              <option key={entry} value={entry}>
                {ROSTER_GROUP_MODE_LABELS[entry]}
              </option>
            ))}
          </select>
        </div>
        <input
          type="search"
          aria-label="Filter valkyries"
          data-testid="roster-filter"
          placeholder="Filter valkyries..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="niuu:w-full niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary niuu:placeholder:text-text-muted"
        />
      </div>
      <div className="niuu:min-h-0 niuu:flex-1 niuu:overflow-auto niuu:p-3">
        {groups.map((group) => {
          const collapseKey = `${mode}:${group.key}`;
          const isCollapsed = collapsed.has(collapseKey);
          return (
            <section key={group.key} data-testid={`roster-group-${group.key}`} className="niuu:mb-4">
              <button
                type="button"
                onClick={() => toggleGroup(collapseKey)}
                aria-expanded={!isCollapsed}
                className="niuu:mb-2 niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:gap-2 niuu:px-1"
              >
                <span className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
                  <ChevronDown
                    size={13}
                    aria-hidden="true"
                    className={isCollapsed ? 'niuu:-rotate-90' : undefined}
                  />
                  {mode === 'flock' ? <Users size={13} aria-hidden="true" /> : kindIcon(group.kind)}
                  <span className="niuu:truncate">{group.label}</span>
                </span>
                <span className="niuu:text-[11px] niuu:text-text-muted">
                  {group.entries.length}
                </span>
              </button>
              {isCollapsed ? null : (
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {group.entries.map((entry) => (
                    <RosterRow
                      key={entry.valkyrie.id}
                      entry={entry}
                      bars={rosterActivityBars(events, entry.valkyrie, now)}
                      selected={selectedId === entry.valkyrie.id}
                      onSelect={onSelect}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })}
        {groups.length === 0 ? (
          <p data-testid="roster-empty" className="niuu:px-1 niuu:text-xs niuu:text-text-muted">
            No valkyries match &quot;{query.trim()}&quot;.
          </p>
        ) : null}
      </div>
    </aside>
  );
}
