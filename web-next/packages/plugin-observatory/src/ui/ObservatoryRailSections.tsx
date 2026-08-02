import type { ReactNode } from 'react';
import type { Topology, TopologyNode } from '../domain';
import { deriveAgentMeshes } from '../domain/agentMesh';
import { computeClassMap, type ComputeClass } from '../domain/computeClass';
import { nodesOfType, RESIDENT_TYPE_IDS } from '../domain/observatoryStats';
import {
  clusterSummaries,
  meshSubtitle,
  meshOf,
  mimirBadge,
  nodeIndex,
  placementSubtitle,
  residentSubtitle,
} from '../domain/railSummaries';
import { CollapsibleSection } from './CollapsibleSection';
import { humanizeObservatoryText } from './displayLabels';
import './ObservatoryRailSections.css';

interface Props {
  topology: Topology | null;
  selectedId: string | null;
  /**
   * `focus` says whether picking this row is also a request to go there.
   *
   * A resident row is: the node may be off screen. A mesh row is not — its
   * members are scattered across clusters, which is the point of a mesh, so
   * flying to whichever one sorts first frames the least representative thing
   * in it and hides the rest.
   */
  onSelect: (nodeId: string, options?: { focus?: boolean }) => void;
}

/**
 * A rail row: a coloured stripe, a name over its placement, and a figure.
 *
 * The stripe carries the compute class — the same green and blue the canvas
 * uses — so scanning the rail and scanning the graph answer the same question
 * the same way. `tone` overrides it where a row is not about a machine: a mesh
 * is amber wherever its members happen to run.
 */
function RailRow({
  id,
  name,
  sub,
  badge,
  badgeTone = 'plain',
  tone,
  selected,
  onSelect,
  testId,
}: {
  id: string;
  name: string;
  sub?: string;
  badge?: ReactNode;
  badgeTone?: 'amber' | 'spring' | 'plain' | 'quiet';
  tone: ComputeClass | 'mesh';
  selected: boolean;
  onSelect: (nodeId: string) => void;
  /** Defaults to the node id; meshes pass their own so rows stay distinct. */
  testId?: string;
}) {
  return (
    <button
      type="button"
      className={`obs-rail__row${selected ? ' obs-rail__row--on' : ''}`}
      data-tone={tone}
      data-testid={`rail-row-${testId ?? id}`}
      aria-pressed={selected}
      onClick={() => onSelect(id)}
    >
      <span className="obs-rail__stripe" aria-hidden="true" />
      <span className="obs-rail__name">{humanizeObservatoryText(name)}</span>
      {sub ? <SubLine text={sub} /> : null}
      {badge != null ? (
        <em className="obs-rail__badge" data-tone={badgeTone}>
          {badge}
        </em>
      ) : null}
    </button>
  );
}

/**
 * The line under a row's name: first token carries the row's colour, the rest
 * is muted.
 *
 * Colouring the whole line put a lit string on every row and the column read
 * as decoration. One toned word per row is enough to group them by eye — what
 * a thing runs on is the part worth the colour; where it sits is context.
 */
function SubLine({ text }: { text: string }) {
  const [lead, ...rest] = text.split(' · ');
  return (
    <span className="obs-rail__sub">
      <span className="obs-rail__sub-lead">{lead}</span>
      {rest.length > 0 ? <span className="obs-rail__sub-rest"> · {rest.join(' · ')}</span> : null}
    </span>
  );
}

/** Empty sections say why they are empty rather than rendering nothing. */
function Empty({ children }: { children: string }) {
  return <p className="obs-rail__empty">{children}</p>;
}

/**
 * The Observatory rail: meshes, residents, Mímir, and the estate itself.
 *
 * These live in the shell's subnav slot rather than a second column — the
 * platform already gives every plugin one rail, and the mockup's rail is that
 * rail, not an extra one.
 *
 * Sections stay present when their data is absent: a deployment with no
 * residents yet should show an empty Residents section, not a rail that
 * silently changes shape.
 */
export function ObservatoryRailSections({ topology, selectedId, onSelect }: Props) {
  const meshes = deriveAgentMeshes(topology);
  const residents = nodesOfType(topology, RESIDENT_TYPE_IDS);
  const mimirs = nodesOfType(topology, ['mimir']);
  const clusters = clusterSummaries(topology);
  // Placement is resolved through containment once, here: a node's own
  // `cluster` field is often absent, and a row that guesses "local" mislabels
  // a Kubernetes workload as somebody's workstation.
  const byId = nodeIndex(topology);
  const classes = computeClassMap(topology?.nodes ?? []);
  const toneOf = (node: TopologyNode): ComputeClass => classes.get(node.id) ?? 'own';

  return (
    <>
      <CollapsibleSection title="Meshes" meta={meshes.length} testId="rail-meshes">
        {meshes.length === 0 ? (
          <Empty>No agent meshes discovered.</Empty>
        ) : (
          meshes.map((mesh) => (
            <RailRow
              key={mesh.id}
              testId={`mesh-${mesh.id}`}
              // Selecting a mesh focuses a member: the mesh is a derived
              // grouping, not a node the canvas can select on its own.
              id={mesh.memberIds[0] ?? mesh.id}
              name={mesh.label}
              sub={meshSubtitle(mesh, topology)}
              badge={mesh.memberIds.length}
              badgeTone="amber"
              tone="mesh"
              selected={mesh.memberIds.some((member) => member === selectedId)}
              // Marked, not travelled to: the pulse already says who is in it.
              onSelect={(nodeId) => onSelect(nodeId, { focus: false })}
            />
          ))
        )}
      </CollapsibleSection>

      <CollapsibleSection title="Residents" meta={residents.length} testId="rail-residents">
        {residents.length === 0 ? (
          <Empty>No residents reporting.</Empty>
        ) : (
          residents.map((node: TopologyNode) => (
            <RailRow
              key={node.id}
              id={node.id}
              name={node.label}
              sub={residentSubtitle(node, byId) || node.status}
              // The mesh it belongs to, if any — the one relationship a
              // resident has that its placement does not already state. Set
              // quietly: it is a footnote on the row, not its headline.
              badge={meshOf(node)}
              badgeTone="quiet"
              tone={toneOf(node)}
              selected={node.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </CollapsibleSection>

      <CollapsibleSection title="Mímir instances" meta={mimirs.length} testId="rail-mimir">
        {mimirs.length === 0 ? (
          <Empty>No Mímir instances discovered.</Empty>
        ) : (
          mimirs.map((node: TopologyNode) => (
            <RailRow
              key={node.id}
              id={node.id}
              name={node.label}
              sub={placementSubtitle(node, byId) || undefined}
              badge={mimirBadge(node)}
              tone={toneOf(node)}
              selected={node.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </CollapsibleSection>

      {/*
        One section, not two. A realm listed on its own says only that a VLAN
        exists; naming it on its clusters' rows says the same thing and shows
        what is inside it.
      */}
      <CollapsibleSection title="Realms & clusters" meta={clusters.length} testId="rail-clusters">
        {clusters.length === 0 ? (
          <Empty>No clusters discovered.</Empty>
        ) : (
          clusters.map((summary) => (
            <RailRow
              key={summary.node.id}
              id={summary.node.id}
              name={summary.node.label}
              sub={[summary.realm, summary.pods === null ? '' : `${summary.pods} pods`]
                .filter(Boolean)
                .join(' · ')}
              badge={
                summary.hosts === 0
                  ? null
                  : [`${summary.hosts}N`, summary.gpus ? `${summary.gpus}G` : '']
                      .filter(Boolean)
                      .join(' · ')
              }
              badgeTone={summary.gpus ? 'spring' : 'plain'}
              tone={summary.computeClass}
              selected={summary.node.id === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </CollapsibleSection>
    </>
  );
}
