import { useMemo } from 'react';
import { useTopology } from '../application/useTopology';
import { useObservatoryStore, type ObservatoryFilter } from '../application/useObservatoryStore';
import type { TopologyNode } from '../domain';
import './ObservatorySubnav.css';

// ── Filter section config ─────────────────────────────────────────────────────

const AGENT_KINDS = new Set(['ravn_long', 'ravn_raid', 'valkyrie', 'skuld']);
const SERVICE_KINDS = new Set(['service', 'bifrost', 'tyr', 'volundr', 'mimir']);
const DEVICE_KINDS = new Set(['printer', 'vaettir', 'beacon']);
const RAID_KIND = 'raid';

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
    id: 'raids',
    label: 'Raids',
    color: 'var(--brand-500, var(--color-brand))',
    count: (nodes) => nodes.filter((n) => n.typeId === RAID_KIND).length,
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

// ── Raid state → dot color ─────────────────────────────────────────────────

function raidDotColor(state: string | undefined): string {
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

  const allRaids = useMemo(() => nodes.filter((n) => n.typeId === 'raid'), [nodes]);

  const activeRaids = useMemo(() => allRaids.slice(0, 6), [allRaids]);

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

      {/* Section 3: Clusters + Active raids */}
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

        {activeRaids.length > 0 && (
          <>
            <div className="obs-subnav__label obs-subnav__label--sub">
              Active raids <span className="obs-subnav__count">{allRaids.length}</span>
            </div>
            {activeRaids.map((raid) => {
              const color = raidDotColor(raid.state);
              return (
                <button
                  key={raid.id}
                  className="obs-subnav__row"
                  data-testid={`raid-${raid.id}`}
                  onClick={() => store.setSelected(raid.id)}
                >
                  <span
                    className="obs-subnav__dot"
                    style={{ background: color, boxShadow: `0 0 6px ${color}` }}
                  />
                  <span className="obs-subnav__name obs-subnav__name--mono">
                    {raid.purpose ?? raid.label}
                  </span>
                  <span className="obs-subnav__count">{raid.state?.slice(0, 4) ?? '—'}</span>
                </button>
              );
            })}
          </>
        )}
      </div>
    </div>
  );
}
