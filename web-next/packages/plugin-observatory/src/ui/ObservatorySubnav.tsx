import { useMemo } from 'react';
import { useTopology } from '../application/useTopology';
import { useObservatoryStore, type ObservatoryFilter } from '../application/useObservatoryStore';
import type { TopologyNode } from '../domain';
import './ObservatorySubnav.css';

// ── Filter section config ─────────────────────────────────────────────────────

const AGENT_KINDS = new Set(['ravn_long', 'ravn_run', 'valkyrie', 'skuld', 'warden']);
const SERVICE_KINDS = new Set(['service', 'bifrost', 'ting', 'volundr', 'mimir']);
const DEVICE_KINDS = new Set(['printer', 'vaettir', 'beacon']);
const RUN_KIND = 'run';

interface FilterRow {
  id: ObservatoryFilter;
  label: string;
  color: string;
  count: (nodes: TopologyNode[]) => number;
}

const FILTER_ROWS: FilterRow[] = [
  {
    id: 'all',
    label: 'All entities',
    color: 'var(--brand-300, var(--color-brand))',
    count: (nodes) => nodes.length,
  },
  {
    id: 'agents',
    label: 'Agents',
    color: 'var(--brand-200, var(--color-brand))',
    count: (nodes) => nodes.filter((n) => AGENT_KINDS.has(n.typeId)).length,
  },
  {
    id: 'runs',
    label: 'Runs',
    color: 'var(--brand-500, var(--color-brand))',
    count: (nodes) => nodes.filter((n) => n.typeId === RUN_KIND).length,
  },
  {
    id: 'services',
    label: 'Services',
    color: 'var(--brand-300, var(--color-brand))',
    count: (nodes) => nodes.filter((n) => SERVICE_KINDS.has(n.typeId)).length,
  },
  {
    id: 'devices',
    label: 'Devices',
    color: 'var(--color-text-muted)',
    count: (nodes) => nodes.filter((n) => DEVICE_KINDS.has(n.typeId)).length,
  },
];

// ── Run state → dot color ─────────────────────────────────────────────────

function runDotColor(state: string | undefined): string {
  if (state === 'forming') return 'var(--brand-200, var(--color-brand))';
  if (state === 'working') return 'var(--brand-500, var(--color-brand))';
  return 'var(--color-text-muted)';
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ObservatorySubnav() {
  const topology = useTopology();
  const [storeState, store] = useObservatoryStore();
  const { filter, selectedId } = storeState;

  const nodes = useMemo(() => topology?.nodes ?? [], [topology]);

  const realms = useMemo(() => nodes.filter((n) => n.typeId === 'realm'), [nodes]);

  const clusters = useMemo(() => nodes.filter((n) => n.typeId === 'cluster'), [nodes]);

  const allRuns = useMemo(() => nodes.filter((n) => n.typeId === 'run'), [nodes]);

  const activeRuns = useMemo(() => allRuns.slice(0, 6), [allRuns]);

  return (
    <div className="obs-subnav" data-testid="observatory-subnav">
      {/* Section 1: Entity filter */}
      <div className="obs-subnav__section">
        <div className="obs-subnav__label">
          Filter <span className="obs-subnav__label-dot">·</span>
        </div>
        {FILTER_ROWS.map((row) => {
          const count = row.count(nodes);
          const active = filter === row.id;
          return (
            <button
              key={row.id}
              className="obs-subnav__row"
              data-active={active}
              data-testid={`filter-${row.id}`}
              onClick={() => store.setFilter(row.id)}
              aria-pressed={active}
            >
              <span
                className="obs-subnav__dot"
                style={{ background: row.color, boxShadow: `0 0 6px ${row.color}` }}
              />
              <span className="obs-subnav__name">{row.label}</span>
              <span className="obs-subnav__count">{count}</span>
            </button>
          );
        })}
      </div>

      {/* Section 2: Realms */}
      <div className="obs-subnav__section">
        <div className="obs-subnav__label">
          Realms <span className="obs-subnav__count">{realms.length}</span>
        </div>
        {realms.map((realm) => (
          <button
            key={realm.id}
            className="obs-subnav__row"
            data-active={selectedId === realm.id}
            data-testid={`realm-${realm.id}`}
            onClick={() => store.setSelected(realm.id)}
            aria-pressed={selectedId === realm.id}
          >
            <span
              className="obs-subnav__dot"
              style={{
                background: 'var(--brand-300, var(--color-brand))',
                boxShadow: '0 0 6px var(--brand-300, var(--color-brand))',
              }}
            />
            <span className="obs-subnav__name">{realm.label}</span>
            {realm.vlan !== undefined && (
              <span className="obs-subnav__count">vlan {realm.vlan}</span>
            )}
          </button>
        ))}
      </div>

      {/* Section 3: Clusters + Active runs */}
      <div className="obs-subnav__section">
        <div className="obs-subnav__label">
          Clusters <span className="obs-subnav__count">{clusters.length}</span>
        </div>
        {clusters.map((cluster) => (
          <button
            key={cluster.id}
            className="obs-subnav__row"
            data-active={selectedId === cluster.id}
            data-testid={`cluster-${cluster.id}`}
            onClick={() => store.setSelected(cluster.id)}
            aria-pressed={selectedId === cluster.id}
          >
            <span
              className="obs-subnav__dot"
              style={{
                background: 'var(--brand-500, var(--color-brand))',
                boxShadow: '0 0 6px var(--brand-500, var(--color-brand))',
              }}
            />
            <span className="obs-subnav__name">{cluster.label}</span>
            <span className="obs-subnav__count">⎔</span>
          </button>
        ))}

        {activeRuns.length > 0 && (
          <>
            <div className="obs-subnav__label obs-subnav__label--sub">
              Active runs <span className="obs-subnav__count">{allRuns.length}</span>
            </div>
            {activeRuns.map((run) => {
              const color = runDotColor(run.state);
              return (
                <button
                  key={run.id}
                  className="obs-subnav__row"
                  data-testid={`run-${run.id}`}
                  onClick={() => store.setSelected(run.id)}
                >
                  <span
                    className="obs-subnav__dot"
                    style={{ background: color, boxShadow: `0 0 6px ${color}` }}
                  />
                  <span className="obs-subnav__name obs-subnav__name--mono">
                    {run.purpose ?? run.label}
                  </span>
                  <span className="obs-subnav__count">{run.state?.slice(0, 4) ?? '—'}</span>
                </button>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
