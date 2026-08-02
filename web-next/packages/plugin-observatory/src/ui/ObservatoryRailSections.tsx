import type { Topology, TopologyNode } from '../domain';
import { deriveAgentMeshes } from '../domain/agentMesh';
import { nodesOfType, RESIDENT_TYPE_IDS } from '../domain/observatoryStats';
import { CollapsibleSection } from './CollapsibleSection';
import { humanizeObservatoryText } from './displayLabels';
import './ObservatoryRailSections.css';

interface Props {
  topology: Topology | null;
  selectedId: string | null;
  onSelect: (nodeId: string) => void;
}

function placementOf(node: TopologyNode): string {
  const parts = [node.cluster, node.hostId].filter(
    (part): part is string => typeof part === 'string' && part.length > 0,
  );
  return parts.join(' · ');
}

function RailRow({
  id,
  name,
  sub,
  badge,
  selected,
  onSelect,
  testId,
}: {
  id: string;
  name: string;
  sub?: string;
  badge?: string;
  selected: boolean;
  onSelect: (nodeId: string) => void;
  /** Defaults to the node id; meshes pass their own so rows stay distinct. */
  testId?: string;
}) {
  return (
    <button
      type="button"
      className={`obs-rail__row${selected ? ' obs-rail__row--on' : ''}`}
      data-testid={`rail-row-${testId ?? id}`}
      aria-pressed={selected}
      onClick={() => onSelect(id)}
    >
      <span className="obs-rail__name">{humanizeObservatoryText(name)}</span>
      {sub ? <span className="obs-rail__sub">{sub}</span> : null}
      {badge ? <em className="obs-rail__badge">{badge}</em> : null}
    </button>
  );
}

/** Empty sections say why they are empty rather than rendering nothing. */
function Empty({ children }: { children: string }) {
  return <p className="obs-rail__empty">{children}</p>;
}

/**
 * Meshes, residents and Mímir sections for the plugin subnav.
 *
 * These live in the shell's subnav slot rather than a second column: the
 * platform already gives every plugin one rail, and the mockup's rail is that
 * rail, not an extra one. Realms and clusters stay with the subnav's own
 * sections rather than being listed twice.
 *
 * Sections stay present when their data is absent: a deployment with no
 * residents yet should show an empty Residents section, not a rail that
 * silently changes shape.
 */
export function ObservatoryRailSections({ topology, selectedId, onSelect }: Props) {
  const meshes = deriveAgentMeshes(topology);
  const residents = nodesOfType(topology, RESIDENT_TYPE_IDS);
  const mimirs = nodesOfType(topology, ['mimir']);
  const byId = new Map((topology?.nodes ?? []).map((node) => [node.id, node]));

  return (
    <>
      <CollapsibleSection title="Meshes" meta={meshes.length} testId="rail-meshes">
        {meshes.length === 0 ? (
          <Empty>No agent meshes discovered.</Empty>
        ) : (
          meshes.map((mesh) => {
            const first = byId.get(mesh.memberIds[0] ?? '');
            return (
              <RailRow
                key={mesh.id}
                testId={`mesh-${mesh.id}`}
                // Selecting a mesh focuses a member: the mesh is a derived
                // grouping, not a node the canvas can select on its own.
                id={mesh.memberIds[0] ?? mesh.id}
                name={mesh.id}
                sub={first ? placementOf(first) : undefined}
                badge={String(mesh.memberIds.length)}
                selected={mesh.memberIds.some((member) => member === selectedId)}
                onSelect={onSelect}
              />
            );
          })
        )}
      </CollapsibleSection>

      <CollapsibleSection title="Residents" meta={residents.length} testId="rail-residents">
        {residents.length === 0 ? (
          <Empty>No residents reporting.</Empty>
        ) : (
          residents.map((node) => (
            <RailRow
              key={node.id}
              id={node.id}
              name={node.label}
              sub={placementOf(node) || node.status}
              selected={node.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Mímir instances"
        meta={mimirs.length}
        testId="rail-mimir"
        defaultOpen={false}
      >
        {mimirs.length === 0 ? (
          <Empty>No Mímir instances discovered.</Empty>
        ) : (
          mimirs.map((node) => (
            <RailRow
              key={node.id}
              id={node.id}
              name={node.label}
              sub={placementOf(node) || undefined}
              selected={node.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </CollapsibleSection>
    </>
  );
}
