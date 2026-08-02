import { useMemo } from 'react';
import { useTopology } from '../application/useTopology';
import { useObservatoryStore } from '../application/useObservatoryStore';
import './ObservatorySubnav.css';
import { CollapsibleSection } from './CollapsibleSection';
import { ObservatoryRailSections } from './ObservatoryRailSections';

// ── Filter section config ─────────────────────────────────────────────────────

// ── Component ─────────────────────────────────────────────────────────────────

export function ObservatorySubnav() {
  const topology = useTopology();
  const [storeState, store] = useObservatoryStore();
  const { selectedId } = storeState;

  const nodes = useMemo(() => topology?.nodes ?? [], [topology]);

  const realms = useMemo(() => nodes.filter((n) => n.typeId === 'realm'), [nodes]);

  const clusters = useMemo(() => nodes.filter((n) => n.typeId === 'cluster'), [nodes]);

  return (
    <div className="obs-subnav" data-testid="observatory-subnav">
      <ObservatoryRailSections
        topology={topology}
        selectedId={selectedId}
        onSelect={(nodeId) => store.setSelected(nodeId)}
      />

      <CollapsibleSection
        title="Realms"
        testId="realms"
        meta={<span className="obs-subnav__count">{realms.length}</span>}
      >
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
      </CollapsibleSection>

      {/* Clusters */}
      <CollapsibleSection
        title="Clusters"
        testId="clusters"
        meta={<span className="obs-subnav__count">{clusters.length}</span>}
      >
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
      </CollapsibleSection>
    </div>
  );
}
