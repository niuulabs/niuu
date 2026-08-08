/**
 * What a region has to say about itself.
 *
 * A realm or a cluster drawn as a shape says only "something is here". These
 * are the figures that make it say what — how much is inside, how much of it
 * is well, and how much of the estate's work is happening in it.
 *
 * Every figure is derived from the snapshot. Nothing here invents a percentage
 * to fill a dial: a row with no data is a row that is not shown, and a gauge
 * with nothing to measure is `null` rather than zero — an estate that reports
 * no rates is not an idle estate.
 */

import type { Topology, TopologyNode } from './index';
import { RESIDENT_TYPE_IDS } from './observatoryStats';

/** One line of a region's readout. */
export interface RegionStat {
  label: string;
  value: string;
}

export interface RegionReadout {
  id: string;
  title: string;
  rows: RegionStat[];
  /**
   * Share of the entities inside that are well, or null when none are placed.
   *
   * Drawn as the outer arc, and only there. A ring with a gap in it says a
   * region is not well from across the estate, without a word — which is why
   * there is no row of text restating it underneath.
   */
  health: number | null;
  /**
   * Share of the estate's measured traffic that crosses this region, or null
   * when nothing measures any of it.
   *
   * This is the "where is the work happening" figure — the reason two healthy
   * regions do not look identical.
   */
  trafficShare: number | null;
}

const RESIDENTS: ReadonlySet<string> = new Set(RESIDENT_TYPE_IDS);

/** Statuses that mean the thing is doing its job. */
const WELL: ReadonlySet<string> = new Set(['healthy', 'idle', 'observing']);

/**
 * Relations that restate a message counted at another hop.
 *
 * The same rule the estate readout uses, for the same reason: one model call is
 * reported both as the caller reaching its gateway and as that gateway reaching
 * the model. Counting both would say a region carries twice the traffic it does.
 */
const RESTATED_RELATIONS: ReadonlySet<string> = new Set(['uses']);

/** Every node inside a region, at any depth. */
export function descendantsOf(topology: Topology, regionId: string): TopologyNode[] {
  const childrenByParent = new Map<string, TopologyNode[]>();
  for (const node of topology.nodes) {
    if (!node.parentId) continue;
    const bucket = childrenByParent.get(node.parentId);
    if (bucket) bucket.push(node);
    else childrenByParent.set(node.parentId, [node]);
  }

  const found: TopologyNode[] = [];
  const walk = (id: string, seen: Set<string>): void => {
    for (const child of childrenByParent.get(id) ?? []) {
      // A parent cycle is malformed data, not a reason to recurse forever.
      if (seen.has(child.id)) continue;
      seen.add(child.id);
      found.push(child);
      walk(child.id, seen);
    }
  };

  walk(regionId, new Set([regionId]));
  return found;
}

function countOf(nodes: readonly TopologyNode[], typeId: string): number {
  return nodes.filter((node) => node.typeId === typeId).length;
}

/** Messages a minute on edges with both ends inside the given set. */
function rateWithin(topology: Topology, inside: ReadonlySet<string>): number {
  let total = 0;
  for (const edge of topology.edges) {
    if (RESTATED_RELATIONS.has(String(edge.relationType ?? ''))) continue;
    if (typeof edge.ratePerMinute !== 'number') continue;
    // Both ends, so a message is attributed to the region it happened in
    // rather than to every region it was merely addressed from.
    if (!inside.has(edge.sourceId) || !inside.has(edge.targetId)) continue;
    total += edge.ratePerMinute;
  }
  return total;
}

/** Messages a minute across the whole estate, on the same counting rule. */
function estateRate(topology: Topology): number {
  let total = 0;
  for (const edge of topology.edges) {
    if (RESTATED_RELATIONS.has(String(edge.relationType ?? ''))) continue;
    if (typeof edge.ratePerMinute === 'number') total += edge.ratePerMinute;
  }
  return total;
}

/** Compact enough for a readout column: 1200 reads as 1.2k. */
export function formatCount(value: number): string {
  if (value < 1000) return String(Math.round(value));
  if (value < 100_000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  return `${Math.round(value / 1000)}k`;
}

/**
 * The readout for one region, or null when the topology does not hold it.
 */
export function regionReadout(topology: Topology | null, regionId: string): RegionReadout | null {
  if (!topology) return null;
  const region = topology.nodes.find((node) => node.id === regionId);
  if (!region) return null;

  const inside = descendantsOf(topology, regionId);
  const insideIds = new Set(inside.map((node) => node.id));

  const residents = inside.filter((node) => RESIDENTS.has(node.typeId)).length;
  const hosts = countOf(inside, 'host');
  const models = countOf(inside, 'model');
  const clusters = countOf(inside, 'cluster');
  const sessions = countOf(inside, 'run');
  const degraded = inside.filter((node) => !WELL.has(node.status)).length;

  const rows: RegionStat[] = [];
  // Only what the snapshot actually holds. A row of dashes teaches an operator
  // to stop reading the panel.
  if (clusters > 0) rows.push({ label: 'CLUSTERS', value: formatCount(clusters) });
  if (hosts > 0) rows.push({ label: 'HOSTS', value: formatCount(hosts) });
  if (residents > 0) rows.push({ label: 'RESIDENTS', value: formatCount(residents) });
  if (sessions > 0) rows.push({ label: 'SESSIONS', value: formatCount(sessions) });
  if (models > 0) rows.push({ label: 'MODELS', value: formatCount(models) });

  const within = rateWithin(topology, insideIds);
  const across = estateRate(topology);
  if (within > 0) rows.push({ label: 'MSGS/MIN', value: formatCount(within) });

  return {
    id: region.id,
    title: region.label,
    rows,
    health: inside.length > 0 ? (inside.length - degraded) / inside.length : null,
    trafficShare: across > 0 ? within / across : null,
  };
}
