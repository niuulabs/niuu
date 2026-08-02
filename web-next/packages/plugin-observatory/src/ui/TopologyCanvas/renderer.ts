/**
 * Canvas 2D drawing helpers.
 *
 * All functions are pure with respect to their arguments — they only mutate
 * the canvas context they receive.  No React or state imports.
 */

import { curveBundle, curveCatmullRom, curveLinear, line } from 'd3-shape';
import { SERVICE_RUNES } from '@niuulabs/ui';
import type {
  Topology,
  TopologyNode,
  TopologyEdge,
  EdgeKind,
  EdgeRelationType,
} from '../../domain';
import type { EdgeLayer } from '../../domain';
import { edgeLayer } from '../../domain';
import { humanizeObservatoryText } from '../displayLabels';
import type { NodePosition } from './layoutEngine';
import { zoneRadius, HOST_HALF_W, HOST_HALF_H } from './layoutEngine';
import { NODE_SIZE, MIMIR_RUNES, LAYOUT, LOD, LABEL_PX, MESH_PULSE } from './config';
import { cloudPath, drawGlyph, roundRectPath } from './shapes';
import { nodeStyle, type NodeStyle } from './nodeStyle';

// ── Level of detail ───────────────────────────────────────────────────────────

/** Which zoom tier a node's label belongs to. */
export type LabelTier = 'primary' | 'secondary';

/**
 * Entities that carry the story of the topology — the things an operator scans
 * for first. They earn a label before anything else does.
 */
const PRIMARY_LABEL_TYPES: ReadonlySet<string> = new Set(['mimir', 'ravn_long', 'valkyrie', 'run']);

export function labelTier(typeId: string): LabelTier {
  return PRIMARY_LABEL_TYPES.has(typeId) ? 'primary' : 'secondary';
}

/** Zoom at which a given node type's label becomes legible without colliding. */
export function labelTierThreshold(typeId: string): number {
  return labelTier(typeId) === 'primary' ? LOD.PRIMARY : LOD.SECONDARY;
}

/**
 * A label is drawn once the camera is zoomed far enough in that it fits beside
 * its neighbours. Hovered and selected nodes always label, so detail is never
 * unreachable — you can always point at a thing to find out what it is.
 */
export function shouldDrawLabel(typeId: string, zoom: number, emphasised: boolean): boolean {
  if (emphasised) return true;
  return zoom >= labelTierThreshold(typeId);
}

/** True once a node's secondary line has room to sit under its label. */
export function shouldDrawNodeDetail(zoom: number, emphasised: boolean): boolean {
  return emphasised || zoom >= LOD.NODE_DETAIL;
}

/**
 * Convert a screen-pixel size into world units for the current camera, so text
 * stays the same physical size however far out the camera sits. Without this a
 * 10px font renders at 3px when zoomed to 0.3 and the overview is unreadable.
 */
export function worldFontSize(screenPx: number, zoom: number): number {
  if (!Number.isFinite(zoom) || zoom <= 0) return screenPx;
  return screenPx / zoom;
}

/**
 * A world-space size that only *starts* growing once the camera pulls past
 * the reference scale.
 *
 * `worldFontSize` holds a label at a constant screen size, which is right for
 * a node's name — it has to stay readable. It is wrong for a realm: the realm
 * itself shrinks as you zoom out, so a name pinned to 22 screen pixels ends up
 * larger than the region it names. This keeps the name a fixed fraction of the
 * world until 0.5x, then stops it disappearing entirely.
 */
export function regionFontSize(worldPx: number, zoom: number): number {
  if (!Number.isFinite(zoom) || zoom <= 0) return worldPx;
  return worldPx * Math.max(1, 0.5 / zoom);
}

// ── Colour palette ────────────────────────────────────────────────────────────
// These map directly to the ice-theme brand ramp used in the prototype.
const C = {
  ice: [186, 230, 253] as const, // brand-300
  frost: [125, 211, 252] as const, // active / run
  moon: [224, 242, 254] as const, // Mímir / long ravens
  indigo: [147, 197, 253] as const, // Bifröst / skuld
  slate: [148, 163, 184] as const, // muted labels
  dim: [100, 115, 140] as const,
  model: [140, 170, 210] as const,
  valk: [170, 205, 245] as const,
  device: [130, 155, 185] as const,
};

export function rgba([r, g, b]: readonly [number, number, number], a: number): string {
  return `rgba(${r},${g},${b},${a})`;
}

export function nodeColour(typeId: string): readonly [number, number, number] {
  switch (typeId) {
    case 'ting':
    case 'ravn_run':
      return C.frost;
    case 'bifrost':
    case 'skuld':
      return C.indigo;
    case 'volundr':
    case 'ravn_long':
    case 'warden':
    case 'mimir':
      return C.moon;
    case 'valkyrie':
      return C.valk;
    case 'trigger':
    case 'end':
      return C.frost;
    case 'stage':
      return C.ice;
    case 'gate':
    case 'cond':
      return C.indigo;
    case 'resource':
      return C.moon;
    case 'model':
      return C.model;
    case 'service':
    case 'run':
    case 'namespace':
      return C.ice;
    case 'printer':
    case 'vaettir':
    case 'host':
    case 'beacon':
      return C.device;
    default:
      return C.slate;
  }
}

export function identityRune(typeId: string): string {
  if (typeId === 'warden') return 'ᚹ';

  const direct = SERVICE_RUNES[typeId as keyof typeof SERVICE_RUNES];
  if (direct) return direct;

  const alias: Partial<Record<string, keyof typeof SERVICE_RUNES>> = {
    ravn_long: 'ravn',
    ravn_run: 'ravn',
  };
  const key = alias[typeId];
  return key ? SERVICE_RUNES[key] : '';
}

export function nodeIconGlyph(typeId: string): string {
  return identityRune(typeId) || typeId.slice(0, 1).toUpperCase();
}

export function nodeSwatchSize(typeId: string, size = NODE_SIZE[typeId] ?? 6): number {
  if (typeId === 'realm' || typeId === 'cluster' || typeId === 'namespace') return 16;
  return Math.max(20, Math.min(30, size * 2.1));
}

export function workflowLabelPlacement(
  node: TopologyNode,
  size: number,
): { dx: number; dy: number; align: CanvasTextAlign; baseline: CanvasTextBaseline } {
  if (node.typeId === 'trigger' || node.layoutHints?.packGroup === 'entry') {
    return { dx: 0, dy: -(size + 12), align: 'center', baseline: 'alphabetic' };
  }
  if (node.typeId === 'resource' || node.layoutHints?.packGroup === 'resource') {
    return { dx: -(size + 12), dy: 3, align: 'right', baseline: 'middle' };
  }
  if (
    node.typeId === 'gate' ||
    node.typeId === 'cond' ||
    node.layoutHints?.packGroup === 'decision'
  ) {
    return { dx: size + 12, dy: 3, align: 'left', baseline: 'middle' };
  }
  if (node.typeId === 'end' || node.layoutHints?.packGroup === 'exit') {
    return { dx: 0, dy: size + 18, align: 'center', baseline: 'top' };
  }
  return { dx: 0, dy: size + 13, align: 'center', baseline: 'top' };
}

export function structureLabel(node: TopologyNode): string {
  return humanizeObservatoryText(node.label);
}

function drawStructureLabel(
  ctx: CanvasRenderingContext2D,
  node: TopologyNode,
  x: number,
  y: number,
  {
    font,
    color,
    uppercase = false,
  }: {
    font: string;
    color: string;
    uppercase?: boolean;
  },
): void {
  // Plain text, centred. A container is already drawn as a boundary; hanging
  // an entity glyph off its name said the boundary was also a thing, and put
  // two marks on the canvas for one cluster.
  const label = uppercase ? structureLabel(node).toUpperCase() : structureLabel(node);
  ctx.save();
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textAlign = 'center';
  ctx.fillText(label, x, y);
  ctx.restore();
}

export interface StructureLabelBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Where a realm's name sits, for hit-testing.
 *
 * The name is the only part of a realm that can be clicked: the hull covers
 * every cluster inside it, so treating the whole rectangle as a target would
 * make the realm swallow every click meant for its contents.
 */
export function realmLabelBounds(hull: RealmBounds, label: string, zoom: number): RealmBounds {
  const px = regionFontSize(LABEL_PX.REALM * 1.7, zoom);
  const inset = regionFontSize(26, zoom);
  const baseline = hull.y1 - regionFontSize(22, zoom);
  const width = Math.max(label.length * px * 0.68, px * 4);
  return {
    x0: hull.x0 + inset - px * 0.3,
    y0: baseline - px,
    x1: hull.x0 + inset + width,
    y1: baseline + px * 0.35,
  };
}

export function getStructureLabelBounds(
  node: TopologyNode,
  pos: NodePosition,
): StructureLabelBounds | null {
  // A realm's label hangs off its hull, not off its centre, so it cannot be
  // derived from a position alone — `realmLabelBounds` handles it instead.
  if (
    node.typeId !== 'cluster' &&
    node.typeId !== 'namespace' &&
    node.typeId !== 'host' &&
    node.typeId !== 'run'
  ) {
    return null;
  }

  if (node.typeId === 'run') {
    const label = humanizeObservatoryText(node.label);
    const charWidth = 8.1;
    const textWidth = Math.max(label.length * charWidth, 42);
    const radius = Math.max((pos.containerWidth ?? 100) / 2, (pos.containerHeight ?? 100) / 2, 42);
    const labelY = pos.y - radius - 8;
    const fontHeight = 10;
    return {
      x: pos.x - textWidth / 2 - 6,
      y: labelY - fontHeight,
      width: textWidth + 12,
      height: fontHeight + 8,
    };
  }

  const label = structureLabel(node);
  const charWidth = 7.2;
  const textWidth = Math.max(label.length * charWidth, 18);
  const totalWidth = textWidth;
  const radius =
    node.typeId === 'cluster'
      ? (pos.zoneRadius ?? zoneRadius(node.typeId))
      : node.typeId === 'namespace'
        ? Math.max(
            (pos.containerWidth ?? LAYOUT.NAMESPACE_INNER_RADIUS * 2) / 2,
            (pos.containerHeight ?? LAYOUT.NAMESPACE_INNER_RADIUS * 2) / 2,
            LAYOUT.NAMESPACE_INNER_RADIUS,
          )
        : Math.max(
            (pos.containerWidth ?? HOST_HALF_W * 2) / 2,
            (pos.containerHeight ?? HOST_HALF_H * 2) / 2,
          );
  const labelY = pos.y - radius - 4;
  const fontHeight = 10;

  return {
    x: pos.x - totalWidth / 2 - 4,
    y: labelY - fontHeight,
    width: totalWidth + 8,
    height: fontHeight + 8,
  };
}

// ── Stars ─────────────────────────────────────────────────────────────────────

export function drawStars(ctx: CanvasRenderingContext2D, w: number, h: number, now: number): void {
  ctx.save();
  for (let i = 0; i < 26; i++) {
    for (let j = 0; j < 14; j++) {
      const seed = (i * 91 + j * 53) % 997;
      const tw = 0.45 + 0.55 * Math.sin(now / 1400 + seed);
      const x = (seed * 13) % w;
      const y = (seed * 31) % h;
      ctx.fillStyle = `rgba(186,230,253,${0.1 + 0.22 * tw})`;
      ctx.fillRect(x, y, 1, 1);
    }
  }
  ctx.restore();
}

// ── Zone circles (realms + clusters) ─────────────────────────────────────────

export interface RealmBounds {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

/** How far a node reaches from its centre, for hull maths. */
function nodeExtentFor(node: TopologyNode, pos: NodePosition): number {
  if (node.typeId === 'cluster' || node.typeId === 'realm') {
    return pos.zoneRadius ?? zoneRadius(node.typeId);
  }
  if (pos.containerWidth || pos.containerHeight) {
    return Math.max((pos.containerWidth ?? 0) / 2, (pos.containerHeight ?? 0) / 2);
  }
  return NODE_SIZE[node.typeId] ?? 8;
}

/**
 * The rectangle a realm occupies: the bounding box of everything inside it.
 *
 * A realm is a region, not an object — it has no centre and no radius of its
 * own, only the extent of what it contains. Drawing it as a circle sized by a
 * constant meant a realm holding four clusters and a realm holding one were
 * the same size, and the four-cluster one spilled over its own edge.
 *
 * Returns null for a realm with nothing placed inside it: a boundary around
 * nothing is a claim that something is missing.
 */
export function realmBounds(
  realm: TopologyNode,
  nodes: readonly TopologyNode[],
  positions: Map<string, NodePosition>,
  padding = LAYOUT.REALM_HULL_PADDING,
): RealmBounds | null {
  const childrenOf = new Map<string, TopologyNode[]>();
  for (const node of nodes) {
    if (!node.parentId) continue;
    const bucket = childrenOf.get(node.parentId);
    if (bucket) bucket.push(node);
    else childrenOf.set(node.parentId, [node]);
  }

  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;

  const walk = (node: TopologyNode, seen: Set<string>): void => {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    const pos = positions.get(node.id);
    if (pos) {
      const extent = nodeExtentFor(node, pos);
      x0 = Math.min(x0, pos.x - extent);
      y0 = Math.min(y0, pos.y - extent);
      x1 = Math.max(x1, pos.x + extent);
      y1 = Math.max(y1, pos.y + extent);
    }
    for (const child of childrenOf.get(node.id) ?? []) walk(child, seen);
  };

  const seen = new Set<string>([realm.id]);
  for (const child of childrenOf.get(realm.id) ?? []) walk(child, seen);

  if (!Number.isFinite(x0)) return null;
  return { x0: x0 - padding, y0: y0 - padding, x1: x1 + padding, y1: y1 + padding };
}

export function drawZones(
  ctx: CanvasRenderingContext2D,
  nodes: TopologyNode[],
  positions: Map<string, NodePosition>,
  _now: number,
  zoom: number,
): void {
  // World units per screen pixel — keeps container headings a constant size.
  const scale = worldFontSize(1, zoom);
  // Draw larger structural groups first, then smaller containers on top.
  for (const typeId of ['cloud', 'realm', 'cluster', 'namespace'] as const) {
    for (const node of nodes) {
      if (node.typeId !== typeId) continue;
      const pos = positions.get(node.id);
      if (!pos) continue;
      const packed = Math.max((pos.containerWidth ?? 0) / 2, (pos.containerHeight ?? 0) / 2);
      const r =
        typeId === 'namespace'
          ? Math.max(packed, LAYOUT.NAMESPACE_INNER_RADIUS)
          : typeId === 'cloud'
            ? // A cloud is only as big as what it holds. Its lobes sit inside
              // its nominal radius, so the models need headroom or the
              // outermost lands on the rim.
              Math.max(packed * LAYOUT.CLOUD_LOBE_HEADROOM, LAYOUT.CLOUD_MIN_RADIUS)
            : (pos.zoneRadius ?? zoneRadius(typeId));
      const { x: cx, y: cy } = pos;

      if (typeId === 'cloud') {
        // Violet, the colour metered model calls already use, because this is
        // where those calls land. None of it is our silicon.
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        g.addColorStop(0, rgba(OUTSIDE_COLOUR, 0.13));
        g.addColorStop(1, rgba(OUTSIDE_COLOUR, 0.02));
        ctx.fillStyle = g;
        cloudPath(ctx, cx, cy, r);
        ctx.fill();

        ctx.save();
        ctx.setLineDash([worldFontSize(7, zoom), worldFontSize(6, zoom)]);
        ctx.strokeStyle = rgba(OUTSIDE_COLOUR, 0.4);
        ctx.lineWidth = worldFontSize(1.3, zoom);
        cloudPath(ctx, cx, cy, r);
        ctx.stroke();
        ctx.restore();

        drawStructureLabel(ctx, node, cx, cy - r * LAYOUT.CLOUD_LABEL_OFFSET, {
          font: `${worldFontSize(LABEL_PX.CLUSTER, zoom)}px "JetBrains Mono", monospace`,
          color: rgba(OUTSIDE_COLOUR, 0.85),
        });
        continue;
      }

      if (typeId === 'realm') {
        // A region, drawn as one: a rounded rectangle around everything it
        // holds, rather than a circle sized by a constant that its own
        // clusters then overflow.
        const hull = realmBounds(node, nodes, positions);
        if (!hull) continue;
        const w = hull.x1 - hull.x0;
        const h = hull.y1 - hull.y0;
        const corner = Math.min(54, w / 2, h / 2);

        const g = ctx.createLinearGradient(hull.x0, hull.y0, hull.x0, hull.y1);
        g.addColorStop(0, rgba(C.ice, 0.03));
        g.addColorStop(1, rgba(C.ice, 0.008));
        ctx.fillStyle = g;
        roundRectPath(ctx, hull.x0, hull.y0, w, h, corner);
        ctx.fill();

        ctx.save();
        ctx.setLineDash([worldFontSize(14, zoom), worldFontSize(12, zoom)]);
        ctx.strokeStyle = rgba(C.ice, 0.16);
        ctx.lineWidth = worldFontSize(1.5, zoom);
        roundRectPath(ctx, hull.x0, hull.y0, w, h, corner);
        ctx.stroke();
        ctx.restore();

        // Along the bottom edge. Cluster names sit above their circles near
        // the hull's top, so a top-left realm label lands on top of them.
        const px = regionFontSize(LABEL_PX.REALM * 1.7, zoom);
        const inset = regionFontSize(26, zoom);
        const baseline = hull.y1 - regionFontSize(22, zoom);
        ctx.save();
        ctx.font = `600 ${px}px "JetBrains Mono", monospace`;
        ctx.fillStyle = rgba(C.ice, 0.5);
        ctx.textAlign = 'left';
        ctx.fillText(structureLabel(node).toUpperCase(), hull.x0 + inset, baseline);
        const dns = (node as unknown as Record<string, unknown>)['dns'];
        if (typeof dns === 'string' && dns && zoom > LOD.CONTAINER_DETAIL) {
          ctx.font = `${regionFontSize(LABEL_PX.CONTAINER_DETAIL, zoom)}px "JetBrains Mono", monospace`;
          ctx.fillStyle = rgba(C.dim, 0.95);
          ctx.fillText(dns, hull.x0 + inset, baseline - px * 0.95);
        }
        ctx.restore();
      } else if (typeId === 'cluster') {
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        g.addColorStop(0, 'rgba(40,58,88,0.22)');
        g.addColorStop(1, 'rgba(20,28,48,0)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = rgba(C.indigo, 0.26);
        ctx.lineWidth = 0.9;
        ctx.setLineDash([4, 5]);
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);

        drawStructureLabel(ctx, node, cx, cy - r - 4 * scale, {
          font: `${worldFontSize(LABEL_PX.CLUSTER, zoom)}px "JetBrains Mono", monospace`,
          color: rgba(C.ice, 0.58),
        });
      } else {
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        g.addColorStop(0, 'rgba(56,82,118,0.18)');
        g.addColorStop(1, 'rgba(18,26,40,0.02)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = rgba(C.ice, 0.2);
        ctx.lineWidth = 0.85;
        ctx.setLineDash([3, 5]);
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);

        drawStructureLabel(ctx, node, cx, cy - r - 4 * scale, {
          font: `${worldFontSize(LABEL_PX.CLUSTER, zoom)}px "JetBrains Mono", monospace`,
          color: rgba(C.ice, 0.62),
        });
      }
    }
  }
}

// ── Edges (5 kinds) ───────────────────────────────────────────────────────────

export function edgeHash(id: string): number {
  let hash = 5381;
  for (let index = 0; index < id.length; index += 1) {
    hash = (((hash << 5) + hash) ^ id.charCodeAt(index)) >>> 0;
  }
  return hash;
}

export function nodeEdgeRadius(node: TopologyNode | undefined, style?: NodeStyle): number {
  if (!node) return 8;
  if (node.typeId === 'mimir') return LAYOUT.MIMIR_RADIUS;
  if (style) return style.radius + 3;
  if (node.typeId === 'host') return Math.max(HOST_HALF_W, HOST_HALF_H);
  if (node.typeId === 'run') return 50;
  return nodeSwatchSize(node.typeId) / 2 + 3;
}

export function trimToNodeBoundary(
  node: TopologyNode | undefined,
  from: NodePosition,
  toward: NodePosition,
  style?: NodeStyle,
): NodePosition {
  const dx = toward.x - from.x;
  const dy = toward.y - from.y;
  const distance = Math.hypot(dx, dy) || 1;

  // Without a resolved glyph, a host or session is only known by its packed
  // extent, so an edge has to stop at the container wall.
  if (!style && node?.typeId === 'host') {
    const hostRadius = Math.max(
      (from.containerWidth ?? HOST_HALF_W * 2) / 2,
      (from.containerHeight ?? HOST_HALF_H * 2) / 2,
    );
    const t = Math.min(hostRadius / distance, 1);
    return { x: from.x + dx * t, y: from.y + dy * t };
  }

  if (!style && node?.typeId === 'run') {
    const runRadius = Math.max((from.containerWidth ?? 100) / 2, (from.containerHeight ?? 100) / 2);
    const t = Math.min(runRadius / distance, 1);
    return { x: from.x + dx * t, y: from.y + dy * t };
  }

  const radius = nodeEdgeRadius(node, style);
  return {
    x: from.x + (dx / distance) * radius,
    y: from.y + (dy / distance) * radius,
  };
}

export function parentChain(
  node: TopologyNode | undefined,
  nodeById: Map<string, TopologyNode>,
): TopologyNode[] {
  const chain: TopologyNode[] = [];
  let current = node;
  while (current?.parentId) {
    const parent = nodeById.get(current.parentId);
    if (!parent) break;
    chain.push(parent);
    current = parent;
  }
  return chain;
}

export function sharedAncestor(
  srcNode: TopologyNode | undefined,
  dstNode: TopologyNode | undefined,
  nodeById: Map<string, TopologyNode>,
): TopologyNode | undefined {
  if (!srcNode || !dstNode) return undefined;
  if (srcNode.parentId && srcNode.parentId === dstNode.parentId) {
    return nodeById.get(srcNode.parentId);
  }
  const srcAncestors = new Set(parentChain(srcNode, nodeById).map((node) => node.id));
  return parentChain(dstNode, nodeById).find((node) => srcAncestors.has(node.id));
}

export function bundleWaypoint(
  start: NodePosition,
  end: NodePosition,
  anchor: NodePosition,
  pull: number,
): NodePosition {
  return {
    x: anchor.x + (start.x - anchor.x) * pull + (end.x - anchor.x) * (1 - pull) * 0.16,
    y: anchor.y + (start.y - anchor.y) * pull + (end.y - anchor.y) * (1 - pull) * 0.16,
  };
}

export function edgeProfile(
  kind: EdgeKind | string | undefined,
  now: number,
  relationType?: EdgeRelationType,
) {
  switch (relationType ?? kind) {
    case 'manages':
    case 'solid':
      return {
        stroke: rgba(C.indigo, 0.46),
        glow: rgba(C.indigo, 0.13),
        lineWidth: 1.15,
        glowWidth: 3.1,
        dash: [] as number[],
        dashOffset: 0,
        bundleStrength: 0.84,
        bend: 18,
      };
    case 'routes_to':
      return {
        stroke: rgba(C.frost, 0.54),
        glow: rgba(C.frost, 0.17),
        lineWidth: 1.1,
        glowWidth: 3.2,
        dash: [3, 5] as number[],
        dashOffset: -now / 80,
        bundleStrength: 0.8,
        bend: 28,
      };
    case 'signals_to':
      return {
        stroke: rgba(C.frost, 0.66),
        glow: rgba(C.frost, 0.22),
        lineWidth: 1.2,
        glowWidth: 3.4,
        dash: [2, 3] as number[],
        dashOffset: -now / 52,
        bundleStrength: 0.78,
        bend: 30,
      };
    case 'observes':
      return {
        stroke: rgba(C.ice, 0.34),
        glow: rgba(C.ice, 0.1),
        lineWidth: 0.95,
        glowWidth: 2.4,
        dash: [1, 5] as number[],
        dashOffset: -now / 96,
        bundleStrength: 0.9,
        bend: 26,
      };
    case 'dashed-short':
    case 'dashed-anim':
      return {
        stroke: rgba(C.frost, 0.54),
        glow: rgba(C.frost, 0.17),
        lineWidth: 1.1,
        glowWidth: 3.2,
        dash: [3, 5] as number[],
        dashOffset: -now / 80,
        bundleStrength: 0.8,
        bend: 28,
      };
    case 'reads':
      return {
        stroke: rgba(C.moon, 0.34),
        glow: rgba(C.moon, 0.1),
        lineWidth: 0.9,
        glowWidth: 2.4,
        dash: [2, 4] as number[],
        dashOffset: 0,
        bundleStrength: 0.9,
        bend: 22,
      };
    case 'writes':
      return {
        stroke: rgba(C.frost, 0.5),
        glow: rgba(C.frost, 0.15),
        lineWidth: 1.05,
        glowWidth: 2.9,
        dash: [7, 3] as number[],
        dashOffset: -now / 110,
        bundleStrength: 0.86,
        bend: 25,
      };
    case 'dashed-long':
      return {
        stroke: rgba(C.moon, 0.36),
        glow: rgba(C.moon, 0.12),
        lineWidth: 0.95,
        glowWidth: 2.6,
        dash: [6, 4] as number[],
        dashOffset: -now / 120,
        bundleStrength: 0.88,
        bend: 24,
      };
    case 'uses':
      return {
        stroke: rgba(C.moon, 0.24),
        glow: rgba(C.moon, 0.08),
        lineWidth: 0.85,
        glowWidth: 2.2,
        dash: [] as number[],
        dashOffset: 0,
        bundleStrength: 0.9,
        bend: 20,
      };
    case 'exposes':
      return {
        stroke: rgba(C.moon, 0.3),
        glow: rgba(C.moon, 0.1),
        lineWidth: 0.9,
        glowWidth: 2.3,
        dash: [8, 6] as number[],
        dashOffset: 0,
        bundleStrength: 0.88,
        bend: 20,
      };
    case 'member_of':
      return {
        stroke: rgba(C.slate, 0.28),
        glow: rgba(C.slate, 0.08),
        lineWidth: 0.8,
        glowWidth: 1.9,
        dash: [1, 4] as number[],
        dashOffset: 0,
        bundleStrength: 0.92,
        bend: 18,
      };
    case 'soft':
      return {
        stroke: rgba(C.moon, 0.22),
        glow: rgba(C.moon, 0.08),
        lineWidth: 0.85,
        glowWidth: 2.2,
        dash: [] as number[],
        dashOffset: 0,
        bundleStrength: 0.9,
        bend: 20,
      };
    case 'run':
      return {
        stroke: rgba(C.frost, 0.42),
        glow: rgba(C.frost, 0.14),
        lineWidth: 1.15,
        glowWidth: 3.0,
        dash: [] as number[],
        dashOffset: 0,
        bundleStrength: 0.76,
        bend: 34,
      };
    default:
      return {
        stroke: rgba(C.slate, 0.34),
        glow: rgba(C.slate, 0.1),
        lineWidth: 0.9,
        glowWidth: 2.4,
        dash: [] as number[],
        dashOffset: 0,
        bundleStrength: 0.84,
        bend: 22,
      };
  }
}

/**
 * Layer colour — what an edge is *for*, rather than which relation verb it
 * happens to use.
 *
 * The relation taxonomy is too fine to colour by (ten verbs, ten hues, no
 * legend anyone can hold in their head), so the ramp is applied at the layer
 * the filter bar exposes. Mesh traffic is the only teal on the canvas because
 * it is the only traffic between agents; telemetry and plain platform wiring
 * are deliberately colourless so they recede.
 */
export const LAYER_COLOUR: Readonly<Record<EdgeLayer, readonly [number, number, number]>> = {
  mesh: [45, 212, 191],
  memory: [56, 189, 248],
  inference: [143, 212, 0],
  platform: [94, 108, 134],
  observability: [94, 108, 134],
  signals: [56, 189, 248],
};

/** Model calls leaving the estate are metered, and are drawn as such. */
export const OUTSIDE_COLOUR: readonly [number, number, number] = [139, 124, 240];

export const LAYER_DASH: Readonly<Record<EdgeLayer, readonly number[]>> = {
  mesh: [],
  memory: [1, 4],
  inference: [],
  platform: [],
  observability: [3, 3],
  signals: [],
};

export function edgeRelationLane(edge: TopologyEdge): number {
  switch (edge.relationType ?? edge.kind) {
    case 'signals_to':
      return -4;
    case 'manages':
      return -3;
    case 'writes':
      return -2;
    case 'member_of':
      return -1;
    case 'uses':
    case 'observes':
      return 1;
    case 'reads':
    case 'exposes':
      return 2;
    case 'routes_to':
      return 3;
    case 'run':
      return 0;
    default:
      return edgeHash(edge.id) % 2 === 0 ? 1 : -1;
  }
}

export function edgeDrawPriority(edge: TopologyEdge): number {
  switch (edge.relationType ?? edge.kind) {
    case 'contains':
      return -1;
    case 'member_of':
    case 'soft':
    case 'uses':
    case 'exposes':
      return 0;
    case 'observes':
    case 'reads':
      return 1;
    case 'routes_to':
    case 'writes':
      return 2;
    case 'manages':
    case 'signals_to':
      return 3;
    case 'run':
      return 4;
    default:
      return 1;
  }
}

export function crossContainerRoutePoints(
  start: NodePosition,
  end: NodePosition,
  ancestor: NodePosition,
  edge: TopologyEdge,
): Array<{ x: number; y: number }> {
  const vx = end.x - start.x;
  const vy = end.y - start.y;
  const length = Math.hypot(vx, vy) || 1;
  const nx = -vy / length;
  const ny = vx / length;
  const midX = (start.x + end.x) / 2;
  const midY = (start.y + end.y) / 2;
  let awayX = midX - ancestor.x;
  let awayY = midY - ancestor.y;
  let awayLen = Math.hypot(awayX, awayY);
  const hashSign = edgeHash(edge.id) % 2 === 0 ? 1 : -1;

  if (awayLen < 1) {
    awayX = nx * hashSign;
    awayY = ny * hashSign;
    awayLen = 1;
  }

  const lane = edgeRelationLane(edge);
  const laneSign = lane === 0 ? hashSign : Math.sign(lane);
  const bow = Math.min(96, Math.max(34, length * 0.22));
  const laneOffset = Math.min(28, Math.abs(lane) * 5) * laneSign;
  const offsetX = (awayX / awayLen) * bow + nx * laneOffset;
  const offsetY = (awayY / awayLen) * bow + ny * laneOffset;

  return [
    start,
    {
      x: start.x + vx * 0.26 + offsetX * 0.72,
      y: start.y + vy * 0.26 + offsetY * 0.72,
    },
    {
      x: midX + offsetX,
      y: midY + offsetY,
    },
    {
      x: start.x + vx * 0.74 + offsetX * 0.72,
      y: start.y + vy * 0.74 + offsetY * 0.72,
    },
    end,
  ];
}

function drawEdge(
  ctx: CanvasRenderingContext2D,
  edge: TopologyEdge,
  nodeById: Map<string, TopologyNode>,
  positions: Map<string, NodePosition>,
  now: number,
  options: EdgeDrawOptions = {},
): void {
  if (edge.sourceId === edge.targetId) return;
  const src = positions.get(edge.sourceId);
  const dst = positions.get(edge.targetId);
  if (!src || !dst) return;
  const srcNode = nodeById.get(edge.sourceId);
  const dstNode = nodeById.get(edge.targetId);
  const directParentChild = srcNode?.id === dstNode?.parentId || dstNode?.id === srcNode?.parentId;
  if (
    edge.relationType === 'contains' ||
    (directParentChild && edge.kind === 'soft' && !edge.label)
  ) {
    return;
  }
  const styleFor = options.styleFor;
  const start = trimToNodeBoundary(srcNode, src, dst, srcNode && styleFor?.(srcNode));
  const end = trimToNodeBoundary(dstNode, dst, src, dstNode && styleFor?.(dstNode));
  const profile = edgeProfile(edge.kind, now, edge.relationType);

  // The layer ramp replaces the profile's hue; the profile keeps the geometry
  // and dash animation that make each relation legible in motion.
  const layer = edgeLayer(edge);
  const outsideCall =
    layer === 'inference' && dstNode && styleFor?.(dstNode).computeClass === 'outside';
  const colour = outsideCall ? OUTSIDE_COLOUR : LAYER_COLOUR[layer];
  const emphasis = options.alphaFor?.(edge) ?? 1;
  const layerDash = LAYER_DASH[layer];
  if (layerDash.length > 0) profile.dash = [...layerDash];
  profile.stroke = rgba(colour, 0.55 * emphasis);
  profile.glow = rgba(colour, 0.16 * emphasis);

  ctx.save();
  ctx.lineCap = 'round';
  ctx.setLineDash(profile.dash);
  ctx.lineDashOffset = profile.dashOffset;

  const vx = end.x - start.x;
  const vy = end.y - start.y;
  const length = Math.hypot(vx, vy) || 1;
  const nx = -vy / length;
  const ny = vx / length;
  const sign = edgeHash(edge.id) % 2 === 0 ? 1 : -1;
  const ancestorNode = sharedAncestor(srcNode, dstNode, nodeById);
  const sharedParentNode = srcNode?.parentId ? nodeById.get(srcNode.parentId) : undefined;
  const srcParentNode = srcNode?.parentId ? nodeById.get(srcNode.parentId) : undefined;
  const dstParentNode = dstNode?.parentId ? nodeById.get(dstNode.parentId) : undefined;
  const sameParent =
    srcNode?.parentId != null &&
    srcNode.parentId === dstNode?.parentId &&
    srcNode.parentId !== null;
  const sameRunFlow = sameParent && sharedParentNode?.typeId === 'run';
  const sameContainerEdge =
    sameParent &&
    (sharedParentNode?.typeId === 'cluster' || sharedParentNode?.typeId === 'namespace') &&
    (edge.kind === 'soft' || edge.kind === 'solid' || Boolean(edge.relationType));
  const crossContainerEdge =
    Boolean(
      ancestorNode &&
      srcParentNode &&
      dstParentNode &&
      srcParentNode.id !== dstParentNode.id &&
      (srcParentNode.typeId === 'namespace' ||
        dstParentNode.typeId === 'namespace' ||
        srcParentNode.typeId === 'cluster' ||
        dstParentNode.typeId === 'cluster'),
    ) &&
    (edge.kind === 'soft' || edge.kind === 'solid' || Boolean(edge.relationType));
  const offset = Math.min(
    profile.bend *
      (sameRunFlow ? 0.32 : sameContainerEdge ? 0.8 : sameParent ? 1.2 : 1) *
      (directParentChild ? 0.7 : 1),
    length * 0.28,
  );
  const midX = (start.x + end.x) / 2;
  const midY = (start.y + end.y) / 2;
  let cx = midX + nx * offset * sign;
  let cy = midY + ny * offset * sign;

  if (ancestorNode && !sameRunFlow && !sameContainerEdge) {
    const ancestorPos = positions.get(ancestorNode.id);
    if (ancestorPos && !directParentChild) {
      const awayX = midX - ancestorPos.x;
      const awayY = midY - ancestorPos.y;
      const awayLen = Math.hypot(awayX, awayY) || 1;
      const outward = Math.min(length * 0.32, profile.bend + 34);
      cx = midX + (awayX / awayLen) * outward;
      cy = midY + (awayY / awayLen) * outward;
    }
  }
  const ancestorPos = ancestorNode ? positions.get(ancestorNode.id) : undefined;

  const edgeLine = line<{ x: number; y: number }>()
    .x((point) => point.x)
    .y((point) => point.y)
    .curve(
      sameRunFlow
        ? curveLinear
        : sameContainerEdge || crossContainerEdge
          ? curveCatmullRom.alpha(0.5)
          : ancestorNode && !directParentChild && !sameRunFlow
            ? curveBundle.beta(profile.bundleStrength)
            : curveCatmullRom.alpha(0.72),
    )
    .context(ctx);
  let points: Array<{ x: number; y: number }> = [start];
  if (sameRunFlow) {
    points.push(end);
  } else if (sameContainerEdge) {
    const lane = edgeRelationLane(edge);
    const laneSign = lane === 0 ? sign : Math.sign(lane);
    const laneWidth = Math.min(18, Math.abs(lane) * 5);
    const bow = Math.min(64, Math.max(20, length * 0.16)) * laneSign + laneWidth * laneSign;
    points.push(
      {
        x: start.x + (end.x - start.x) * 0.3 + nx * bow,
        y: start.y + (end.y - start.y) * 0.3 + ny * bow,
      },
      {
        x: start.x + (end.x - start.x) * 0.7 + nx * bow,
        y: start.y + (end.y - start.y) * 0.7 + ny * bow,
      },
    );
    points.push(end);
  } else if (crossContainerEdge && ancestorPos) {
    points = crossContainerRoutePoints(start, end, ancestorPos, edge);
  } else if (ancestorPos && !directParentChild) {
    points.push(bundleWaypoint(start, end, ancestorPos, 0.34));
    points.push({
      x: ancestorPos.x + (cx - ancestorPos.x) * 0.88,
      y: ancestorPos.y + (cy - ancestorPos.y) * 0.88,
    });
    points.push(bundleWaypoint(end, start, ancestorPos, 0.34));
  } else {
    const controlStart = {
      x: start.x + (cx - start.x) * (sameRunFlow ? 0.32 : 0.55),
      y: start.y + (cy - start.y) * (sameRunFlow ? 0.32 : 0.55),
    };
    const controlEnd = {
      x: end.x + (cx - end.x) * (sameRunFlow ? 0.32 : 0.55),
      y: end.y + (cy - end.y) * (sameRunFlow ? 0.32 : 0.55),
    };
    points.push(controlStart, { x: cx, y: cy }, controlEnd);
    points.push(end);
  }

  ctx.strokeStyle = profile.glow;
  ctx.lineWidth = profile.glowWidth;
  ctx.beginPath();
  edgeLine(points);
  ctx.stroke();

  ctx.strokeStyle = profile.stroke;
  ctx.lineWidth = profile.lineWidth;
  ctx.beginPath();
  edgeLine(points);
  ctx.stroke();

  if (sameParent && !directParentChild && ancestorPos && !sameRunFlow && !sameContainerEdge) {
    const awayX = midX - ancestorPos.x;
    const awayY = midY - ancestorPos.y;
    const awayLen = Math.hypot(awayX, awayY) || 1;
    const sparkleX = ancestorPos.x + (awayX / awayLen) * Math.min(52, length * 0.18);
    const sparkleY = ancestorPos.y + (awayY / awayLen) * Math.min(52, length * 0.18);
    ctx.fillStyle = profile.stroke;
    ctx.beginPath();
    ctx.arc(sparkleX, sparkleY, 1.4, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.restore();
}

export interface EdgeDrawOptions {
  /** Resolved glyph for a node, so an edge stops at its outline. */
  styleFor?: (node: TopologyNode) => NodeStyle;
  /** 0–1 emphasis, used to fade everything the operator is not tracing. */
  alphaFor?: (edge: TopologyEdge) => number;
}

export function drawEdges(
  ctx: CanvasRenderingContext2D,
  topology: Topology,
  positions: Map<string, NodePosition>,
  now: number,
  options: EdgeDrawOptions = {},
): void {
  ctx.save();
  const nodeById = new Map(topology.nodes.map((node) => [node.id, node]));
  const edges = [...topology.edges].sort((a, b) => {
    const priority = edgeDrawPriority(a) - edgeDrawPriority(b);
    return priority === 0 ? a.id.localeCompare(b.id) : priority;
  });
  for (const edge of edges) {
    drawEdge(ctx, edge, nodeById, positions, now, options);
  }
  ctx.restore();
}

/**
 * The faint boundary drawn around a container that packs children.
 *
 * Hosts and workflow sessions are both a *thing* and a *place* — a rack with
 * agents on it, a session with agents in it. The glyph says what it is; this
 * ring says where its contents end. A container with nothing inside it gets no
 * ring, because an empty boundary reads as a missing child rather than none.
 */
function drawContainerRing(
  ctx: CanvasRenderingContext2D,
  pos: NodePosition,
  radius: number,
  colour: readonly [number, number, number],
  alpha: number,
  zoom: number,
): void {
  ctx.save();
  const glow = ctx.createRadialGradient(pos.x, pos.y, radius * 0.35, pos.x, pos.y, radius);
  glow.addColorStop(0, rgba(colour, 0.05 * alpha));
  glow.addColorStop(1, rgba(colour, 0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = rgba(colour, 0.22 * alpha);
  ctx.lineWidth = worldFontSize(0.9, zoom);
  ctx.setLineDash([worldFontSize(5, zoom), worldFontSize(6, zoom)]);
  ctx.beginPath();
  ctx.arc(pos.x, pos.y, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

// ── Mímir ─────────────────────────────────────────────────────────────────────

export interface MimirDrawOptions {
  scale?: number;
  label?: string;
  /** Compute-class hue, so a Mímir on your own hardware reads green. */
  colour?: readonly [number, number, number];
  alpha?: number;
  zoom?: number;
  reducedMotion?: boolean;
}

/**
 * Mímir — a well, not a node.
 *
 * Memory is the one thing on this canvas everything else reaches into, so it
 * gets the only mark with depth: dark water lit at the rim, ripples rising
 * outward, and runes turning around it. The runes only appear once the camera
 * is close enough for them to be glyphs rather than noise.
 */
export function drawMimir(
  ctx: CanvasRenderingContext2D,
  pos: NodePosition,
  now: number,
  options: MimirDrawOptions = {},
): void {
  const scale = options.scale ?? 1;
  const label = options.label ?? 'MÍMIR';
  const colour = options.colour ?? C.moon;
  const a = options.alpha ?? 1;
  const zoom = options.zoom ?? 1;
  const R = LAYOUT.MIMIR_RADIUS * scale;
  const { x, y } = pos;
  const breathe = options.reducedMotion ? 1 : 1 + 0.04 * Math.sin(now / 1900);

  // The halo that says "everything here reads from this".
  const neb = ctx.createRadialGradient(x, y, R * 0.9, x, y, R * 3);
  neb.addColorStop(0, rgba(colour, 0.1 * a));
  neb.addColorStop(1, rgba(colour, 0));
  ctx.fillStyle = neb;
  ctx.beginPath();
  ctx.arc(x, y, R * 3, 0, Math.PI * 2);
  ctx.fill();

  // Dark water, tinted at the rim by the compute hue.
  const water = ctx.createRadialGradient(x, y, R * 0.1, x, y, R * breathe);
  water.addColorStop(0, `rgba(4,8,15,${a})`);
  water.addColorStop(0.72, `rgba(5,12,22,${a})`);
  water.addColorStop(1, rgba(colour, 0.22 * a));
  ctx.fillStyle = water;
  ctx.beginPath();
  ctx.arc(x, y, R * breathe, 0, Math.PI * 2);
  ctx.fill();

  for (let i = 1; i <= 3; i += 1) {
    const phase = options.reducedMotion ? 0.55 : (now / 3800 + i / 3) % 1;
    ctx.strokeStyle = rgba(colour, 0.26 * (1 - phase) * a);
    ctx.lineWidth = worldFontSize(1.4, zoom);
    ctx.beginPath();
    ctx.arc(x, y, R * (0.16 + phase * 0.8), 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.strokeStyle = rgba(colour, 0.72 * a);
  ctx.lineWidth = worldFontSize(2.2, zoom);
  ctx.beginPath();
  ctx.arc(x, y, R * breathe, 0, Math.PI * 2);
  ctx.stroke();

  if (zoom > 0.55) {
    const n = MIMIR_RUNES.length;
    ctx.save();
    ctx.font = `${worldFontSize(11, zoom)}px "JetBrains Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const spin = options.reducedMotion ? 0 : now / 24000;
    for (let i = 0; i < n; i += 1) {
      const t = (i / n) * Math.PI * 2 + spin;
      ctx.fillStyle = rgba(colour, (0.4 + 0.3 * Math.sin(now / 850 + i)) * a);
      ctx.fillText(MIMIR_RUNES[i] ?? 'ᚠ', x + Math.cos(t) * R * 1.75, y + Math.sin(t) * R * 1.75);
    }
    ctx.restore();
  }

  ctx.textBaseline = 'alphabetic';
  ctx.textAlign = 'center';
  ctx.fillStyle = rgba(colour, 0.9 * a);
  ctx.font = `${worldFontSize(LABEL_PX.PRIMARY, zoom)}px "JetBrains Mono", monospace`;
  ctx.fillText(label, x, y + R * 1.75 + worldFontSize(19, zoom));
}

// ── Generic nodes ─────────────────────────────────────────────────────────────

export interface NodeDrawOptions {
  /** Animation clock. */
  now?: number;
  /** Resolved glyph, size and hue. Defaults to the boxed dot. */
  style?: NodeStyle;
  /**
   * 0–1 emphasis. Anything the operator is not currently tracing drops back
   * so the few things they are stay legible.
   */
  alpha?: number;
  selected?: boolean;
  reducedMotion?: boolean;
}

/** The second line under a node's name, when the adapters supplied one. */
export function nodeDetailLine(node: TopologyNode): string {
  const extra = node as unknown as Record<string, unknown>;
  for (const key of ['sub', 'detail', 'workload', 'model', 'engine', 'hw']) {
    const value = extra[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return '';
}

export function drawNode(
  ctx: CanvasRenderingContext2D,
  node: TopologyNode,
  pos: NodePosition,
  hovered: boolean,
  zoom: number,
  options: NodeDrawOptions = {},
): void {
  if (node.typeId === 'mimir') return; // drawn last, above everything
  // Containers are drawn as boundaries by `drawZones`; giving one a glyph too
  // would put two marks on the canvas for the same thing.
  if (
    node.typeId === 'realm' ||
    node.typeId === 'cluster' ||
    node.typeId === 'namespace' ||
    node.typeId === 'cloud'
  ) {
    return;
  }

  const now = options.now ?? 0;
  const alpha = options.alpha ?? 1;
  const style = options.style ?? nodeStyle(node, new Map());
  const emphasised = hovered || options.selected === true;
  const { x, y } = pos;

  // Hosts and sessions hold their children: ring first, then the glyph on top.
  if (node.typeId === 'host' || node.typeId === 'run') {
    const packed = Math.max((pos.containerWidth ?? 0) / 2, (pos.containerHeight ?? 0) / 2);
    if (packed > style.radius * 1.6) {
      drawContainerRing(ctx, pos, packed, style.colour, alpha, zoom);
    }
  }

  if (emphasised) {
    ctx.fillStyle = rgba(style.colour, 0.13 * alpha);
    ctx.beginPath();
    ctx.arc(x, y, style.radius + 18, 0, Math.PI * 2);
    ctx.fill();
  }

  drawGlyph(ctx, {
    shape: style.shape,
    x,
    y,
    size: style.size,
    colour: style.colour,
    alpha,
    now,
    zoom,
    reducedMotion: options.reducedMotion,
    state: node.status,
    progress:
      typeof (node as unknown as Record<string, unknown>).utilisation === 'number'
        ? ((node as unknown as Record<string, unknown>).utilisation as number)
        : undefined,
  });

  // Labels resolve with the camera rather than from a fixed type allowlist:
  // the graph is far too dense to label everything at overview zoom.
  if (!shouldDrawLabel(node.typeId, zoom, emphasised)) return;

  const tier = labelTier(node.typeId);
  const px = worldFontSize(tier === 'primary' ? LABEL_PX.PRIMARY : LABEL_PX.SECONDARY, zoom);
  const labelY = y + style.radius + worldFontSize(node.typeId === 'host' ? 15 : 19, zoom);

  ctx.save();
  ctx.fillStyle = rgba(tier === 'primary' ? C.ice : C.slate, (emphasised ? 0.98 : 0.82) * alpha);
  ctx.font = `${emphasised ? 600 : 500} ${px}px "JetBrains Mono", monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(humanizeObservatoryText(node.label), x, labelY);

  const detail = nodeDetailLine(node);
  if (detail && shouldDrawNodeDetail(zoom, emphasised)) {
    ctx.fillStyle = rgba(C.dim, 0.9 * alpha);
    ctx.font = `${worldFontSize(LABEL_PX.NODE_DETAIL, zoom)}px "JetBrains Mono", monospace`;
    ctx.fillText(detail, x, labelY + worldFontSize(13, zoom));
  }
  ctx.restore();
  ctx.textBaseline = 'alphabetic';
}

// ── Minimap ───────────────────────────────────────────────────────────────────

export function drawMinimap(
  ctx: CanvasRenderingContext2D,
  W: number,
  H: number,
  topology: Topology,
  positions: Map<string, NodePosition>,
  camX: number,
  camY: number,
  camZoom: number,
  viewW: number,
  viewH: number,
  worldW: number,
  worldH: number,
): void {
  // The minimap is centred on (0,0) with ±(worldW/2, worldH/2) extent.
  const halfW = worldW / 2;
  const halfH = worldH / 2;
  const sx = W / worldW;
  const sy = H / worldH;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = 'rgba(9,9,11,0.88)';
  ctx.fillRect(0, 0, W, H);

  // Realm outlines
  for (const node of topology.nodes) {
    if (node.typeId !== 'realm') continue;
    const pos = positions.get(node.id);
    if (!pos) continue;
    const mx = (pos.x + halfW) * sx;
    const my = (pos.y + halfH) * sy;
    ctx.strokeStyle = rgba(C.indigo, 0.25);
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    ctx.arc(mx, my, 18 * sx, 0, Math.PI * 2);
    ctx.stroke();
  }

  // Node dots
  for (const node of topology.nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;
    const mx = (pos.x + halfW) * sx;
    const my = (pos.y + halfH) * sy;
    ctx.fillStyle = node.typeId === 'mimir' ? rgba(C.moon, 0.9) : rgba(C.ice, 0.6);
    const r = node.typeId === 'mimir' ? 3 : 1.5;
    ctx.fillRect(mx - r / 2, my - r / 2, r, r);
  }

  // Viewport rectangle
  if (viewW && camZoom) {
    const vw = (viewW / camZoom) * sx;
    const vh = (viewH / camZoom) * sy;
    const vx = (camX - viewW / (2 * camZoom) + halfW) * sx;
    const vy = (camY - viewH / (2 * camZoom) + halfH) * sy;
    ctx.strokeStyle = rgba(C.ice, 0.7);
    ctx.lineWidth = 1;
    ctx.strokeRect(vx, vy, vw, vh);
  }

  // Caption
  ctx.fillStyle = rgba(C.slate, 0.55);
  ctx.font = '8px Inter, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(`${topology.nodes.length} entities`, 4, H - 4);
  ctx.textAlign = 'right';
  ctx.fillText('MINIMAP', W - 4, H - 4);
}

// ── Agent mesh ────────────────────────────────────────────────────────────────

/** One member of the mesh being engaged with, and how big it draws. */
export interface MeshMember {
  x: number;
  y: number;
  /** The member's own outline radius, in world units. */
  radius: number;
}

/**
 * Ring radius and alpha for one pulse phase. `t` runs 0→1 over a cycle.
 *
 * Standoff and travel are screen pixels converted to world units, so the ring
 * is the same size on screen however far the camera sits back. Sized in world
 * units it vanished at exactly the zoom where a cross-cluster mesh is worth
 * looking at.
 */
function pulsePhase(t: number, radius: number, zoom: number): { radius: number; alpha: number } {
  const wrapped = t - Math.floor(t);
  const standoff = worldFontSize(MESH_PULSE.STANDOFF_PX, zoom);
  const travel = worldFontSize(MESH_PULSE.TRAVEL_PX, zoom);
  return {
    radius: radius + standoff + wrapped * travel,
    // Ease the fade out so the ring dissolves rather than snapping off.
    alpha: MESH_PULSE.PEAK_ALPHA * (1 - wrapped) ** 2,
  };
}

/**
 * Mark the members of the agent mesh the operator is engaging with.
 *
 * A mesh spans clusters by design, so its members share no region — the hull
 * this replaced enclosed every unrelated node that happened to lie between
 * them, and read as a container the mesh does not have. Pulsing each member
 * says "these, wherever they are" and claims no territory.
 *
 * Two rings run half a cycle apart so there is always one expanding, over a
 * steady halo that keeps members marked while a ring is faded out. Amber is
 * already the canvas's mesh colour, so the pulse agrees with the mesh edges
 * rather than introducing a second vocabulary for the same thing.
 */
export function drawAgentMesh(
  ctx: CanvasRenderingContext2D,
  members: readonly MeshMember[],
  now: number,
  zoom: number,
  reducedMotion = false,
): void {
  if (members.length === 0) return;

  const cycle = (now % MESH_PULSE.PERIOD_MS) / MESH_PULSE.PERIOD_MS;
  // Reduced motion still needs the members marked — it just holds the rings
  // still instead of animating them.
  const phases = reducedMotion
    ? [{ t: 0.3, dim: 1 }]
    : [
        { t: cycle, dim: 1 },
        { t: cycle + MESH_PULSE.PHASE_OFFSET, dim: MESH_PULSE.TRAILING_DIM },
      ];
  const halo = worldFontSize(MESH_PULSE.STANDOFF_PX, zoom);

  ctx.save();

  for (const member of members) {
    // A filled wash first: at low zoom the member's glyph is a few pixels, so
    // an outline alone has almost nothing to outline.
    const glow = ctx.createRadialGradient(
      member.x,
      member.y,
      0,
      member.x,
      member.y,
      member.radius + halo,
    );
    glow.addColorStop(0, rgba(LAYER_COLOUR.mesh, MESH_PULSE.GLOW_ALPHA));
    glow.addColorStop(1, rgba(LAYER_COLOUR.mesh, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(member.x, member.y, member.radius + halo, 0, Math.PI * 2);
    ctx.fill();

    ctx.lineWidth = worldFontSize(MESH_PULSE.HALO_LINE_WIDTH, zoom);
    ctx.beginPath();
    ctx.arc(member.x, member.y, member.radius + halo, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(LAYER_COLOUR.mesh, MESH_PULSE.HALO_ALPHA);
    ctx.stroke();

    ctx.lineWidth = worldFontSize(MESH_PULSE.LINE_WIDTH, zoom);
    for (const phase of phases) {
      const ring = pulsePhase(phase.t, member.radius, zoom);
      if (ring.alpha <= 0.01) continue;
      ctx.beginPath();
      ctx.arc(member.x, member.y, ring.radius, 0, Math.PI * 2);
      ctx.strokeStyle = rgba(LAYER_COLOUR.mesh, ring.alpha * phase.dim);
      ctx.stroke();
    }
  }

  ctx.restore();
}
