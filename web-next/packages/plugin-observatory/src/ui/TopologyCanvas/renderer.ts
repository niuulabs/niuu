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
import { humanizeObservatoryText } from '../displayLabels';
import type { NodePosition } from './layoutEngine';
import { zoneRadius, HOST_HALF_W, HOST_HALF_H } from './layoutEngine';
import { NODE_SIZE, MIMIR_RUNES, LAYOUT, LOD, LABEL_PX, MESH_HULL } from './config';
import type { Point } from './canvasMath';
import { centroid, convexHull, expandFromCentroid } from './canvasMath';

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

function drawNodeSwatch(
  ctx: CanvasRenderingContext2D,
  typeId: string,
  cx: number,
  cy: number,
  size = NODE_SIZE[typeId] ?? 6,
  hovered = false,
): void {
  const col = nodeColour(typeId);
  const box = nodeSwatchSize(typeId, size);
  const half = box / 2;
  const radius = Math.max(5, box * 0.27);

  ctx.save();
  if (hovered) {
    ctx.strokeStyle = rgba(C.moon, 0.72);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.roundRect(cx - half - 4, cy - half - 4, box + 8, box + 8, radius + 3);
    ctx.stroke();
  }

  ctx.fillStyle = rgba(col, hovered ? 0.2 : 0.14);
  ctx.strokeStyle = rgba(col, hovered ? 1 : 0.9);
  ctx.lineWidth = hovered ? 1.3 : 1;
  ctx.beginPath();
  ctx.roundRect(cx - half, cy - half, box, box, radius);
  ctx.fill();
  ctx.stroke();

  ctx.fillStyle = rgba(col, 0.96);
  ctx.font = `700 ${Math.max(10, Math.round(box * 0.55))}px "JetBrains Mono", monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(nodeIconGlyph(typeId), cx, cy + 0.5);
  ctx.textBaseline = 'alphabetic';
  ctx.restore();
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
    scale = 1,
  }: {
    font: string;
    color: string;
    uppercase?: boolean;
    /** World units per screen pixel, so the glyph and gaps track the font. */
    scale?: number;
  },
): void {
  const label = uppercase ? structureLabel(node).toUpperCase() : structureLabel(node);
  ctx.save();
  ctx.font = font;
  const metrics = ctx.measureText?.(label);
  const textWidth = metrics?.width ?? label.length * 7.2 * scale;
  const glyphGap = 8 * scale;
  const glyphWidth = 16 * scale;
  const startX = x - (textWidth + glyphGap + glyphWidth) / 2;
  const glyphX = startX + glyphWidth / 2;
  const textX = startX + glyphWidth + glyphGap;

  drawNodeSwatch(
    ctx,
    node.typeId,
    glyphX,
    y - 4 * scale,
    (NODE_SIZE[node.typeId] ?? 6) * scale,
    false,
  );
  ctx.fillStyle = color;
  ctx.font = font;
  ctx.textAlign = 'left';
  ctx.fillText(label, textX, y);
  ctx.restore();
}

export interface StructureLabelBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function getStructureLabelBounds(
  node: TopologyNode,
  pos: NodePosition,
): StructureLabelBounds | null {
  if (
    node.typeId !== 'realm' &&
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

  const label = node.typeId === 'realm' ? structureLabel(node).toUpperCase() : structureLabel(node);
  const glyphGap = 8;
  const glyphWidth = 16;
  const charWidth = node.typeId === 'realm' ? 7.8 : 7.2;
  const textWidth = Math.max(label.length * charWidth, 18);
  const totalWidth = glyphWidth + glyphGap + textWidth;
  const radius =
    node.typeId === 'realm' || node.typeId === 'cluster'
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
  const labelY = pos.y - radius - (node.typeId === 'realm' ? 8 : 4);
  const fontHeight = node.typeId === 'realm' ? 13 : 10;

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

export function drawZones(
  ctx: CanvasRenderingContext2D,
  nodes: TopologyNode[],
  positions: Map<string, NodePosition>,
  now: number,
  zoom: number,
): void {
  // World units per screen pixel — keeps container headings a constant size.
  const scale = worldFontSize(1, zoom);
  // Draw larger structural groups first, then smaller containers on top.
  for (const typeId of ['realm', 'cluster', 'namespace'] as const) {
    for (const node of nodes) {
      if (node.typeId !== typeId) continue;
      const pos = positions.get(node.id);
      if (!pos) continue;
      const r =
        typeId === 'namespace'
          ? Math.max(
              (pos.containerWidth ?? LAYOUT.NAMESPACE_INNER_RADIUS * 2) / 2,
              (pos.containerHeight ?? LAYOUT.NAMESPACE_INNER_RADIUS * 2) / 2,
              LAYOUT.NAMESPACE_INNER_RADIUS,
            )
          : (pos.zoneRadius ?? zoneRadius(typeId));
      const { x: cx, y: cy } = pos;

      if (typeId === 'realm') {
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
        g.addColorStop(0, 'rgba(30,48,78,0.38)');
        g.addColorStop(0.65, 'rgba(20,32,56,0.16)');
        g.addColorStop(1, 'rgba(14,20,36,0.02)');
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();

        const pulse = 0.28 + 0.06 * Math.sin(now / 5000 + node.id.charCodeAt(0) * 0.1);
        ctx.strokeStyle = rgba(C.indigo, pulse);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();

        drawStructureLabel(ctx, node, cx, cy - r - 8 * scale, {
          font: `600 ${worldFontSize(LABEL_PX.REALM, zoom)}px Inter, sans-serif`,
          color: rgba(C.ice, 0.78),
          uppercase: true,
          scale,
        });
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
          scale,
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
          scale,
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

export function nodeEdgeRadius(node: TopologyNode | undefined): number {
  if (!node) return 8;
  if (node.typeId === 'mimir') return LAYOUT.MIMIR_RADIUS;
  if (node.typeId === 'host') return Math.max(HOST_HALF_W, HOST_HALF_H);
  if (node.typeId === 'run') return 50;
  return nodeSwatchSize(node.typeId) / 2 + 3;
}

export function trimToNodeBoundary(
  node: TopologyNode | undefined,
  from: NodePosition,
  toward: NodePosition,
): NodePosition {
  const dx = toward.x - from.x;
  const dy = toward.y - from.y;
  const distance = Math.hypot(dx, dy) || 1;

  if (node?.typeId === 'host') {
    const hostRadius = Math.max(
      (from.containerWidth ?? HOST_HALF_W * 2) / 2,
      (from.containerHeight ?? HOST_HALF_H * 2) / 2,
    );
    const t = Math.min(hostRadius / distance, 1);
    return { x: from.x + dx * t, y: from.y + dy * t };
  }

  if (node?.typeId === 'run') {
    const runRadius = Math.max((from.containerWidth ?? 100) / 2, (from.containerHeight ?? 100) / 2);
    const t = Math.min(runRadius / distance, 1);
    return { x: from.x + dx * t, y: from.y + dy * t };
  }

  const radius = nodeEdgeRadius(node);
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
  const start = trimToNodeBoundary(srcNode, src, dst);
  const end = trimToNodeBoundary(dstNode, dst, src);
  const profile = edgeProfile(edge.kind, now, edge.relationType);

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

export function drawEdges(
  ctx: CanvasRenderingContext2D,
  topology: Topology,
  positions: Map<string, NodePosition>,
  now: number,
): void {
  ctx.save();
  const nodeById = new Map(topology.nodes.map((node) => [node.id, node]));
  const edges = [...topology.edges].sort((a, b) => {
    const priority = edgeDrawPriority(a) - edgeDrawPriority(b);
    return priority === 0 ? a.id.localeCompare(b.id) : priority;
  });
  for (const edge of edges) {
    drawEdge(ctx, edge, nodeById, positions, now);
  }
  ctx.restore();
}

function drawHost(
  ctx: CanvasRenderingContext2D,
  node: TopologyNode,
  pos: NodePosition,
  hovered: boolean,
): void {
  const hullW = pos.containerWidth ?? HOST_HALF_W * 2;
  const hullH = pos.containerHeight ?? HOST_HALF_H * 2;
  const hostRadius = Math.max(hullW, hullH) / 2;

  ctx.save();
  const hullGlow = ctx.createRadialGradient(pos.x, pos.y, 0, pos.x, pos.y, hostRadius * 1.24);
  hullGlow.addColorStop(0, hovered ? 'rgba(90,138,196,0.14)' : 'rgba(62,94,138,0.1)');
  hullGlow.addColorStop(1, 'rgba(18,26,40,0)');
  ctx.fillStyle = hullGlow;
  ctx.beginPath();
  ctx.arc(pos.x, pos.y, hostRadius + 10, 0, Math.PI * 2);
  ctx.fill();

  const fill = ctx.createRadialGradient(pos.x, pos.y, hostRadius * 0.12, pos.x, pos.y, hostRadius);
  fill.addColorStop(0, hovered ? 'rgba(24,34,52,0.18)' : 'rgba(20,30,44,0.14)');
  fill.addColorStop(1, hovered ? 'rgba(14,20,34,0.32)' : 'rgba(10,16,28,0.24)');
  ctx.fillStyle = fill;
  ctx.beginPath();
  ctx.arc(pos.x, pos.y, hostRadius, 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = hovered ? rgba(C.indigo, 0.48) : rgba(C.slate, 0.24);
  ctx.lineWidth = 1;
  ctx.setLineDash([5, 6]);
  ctx.beginPath();
  ctx.arc(pos.x, pos.y, hostRadius, 0, Math.PI * 2);
  ctx.stroke();

  ctx.strokeStyle = hovered ? rgba(C.ice, 0.22) : rgba(C.ice, 0.14);
  ctx.lineWidth = 0.8;
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.arc(pos.x, pos.y, Math.max(22, hostRadius - 18), 0, Math.PI * 2);
  ctx.stroke();

  drawStructureLabel(ctx, node, pos.x, pos.y - hostRadius - 8, {
    font: `${hovered ? 600 : 500} 10px "JetBrains Mono", monospace`,
    color: rgba(C.moon, hovered ? 0.92 : 0.74),
  });

  ctx.restore();
}

// ── Mímir ─────────────────────────────────────────────────────────────────────

export function drawMimir(
  ctx: CanvasRenderingContext2D,
  pos: NodePosition,
  now: number,
  scale = 1,
  label = 'MÍMIR',
): void {
  const R = LAYOUT.MIMIR_RADIUS * scale;
  const { x, y } = pos;

  // Nebula glow
  const neb = ctx.createRadialGradient(x, y, 0, x, y, R * 2.6);
  neb.addColorStop(0, rgba([210, 230, 255], 0.62 * Math.min(1, scale + 0.2)));
  neb.addColorStop(0.35, rgba([180, 210, 245], 0.22 * Math.min(1, scale + 0.2)));
  neb.addColorStop(1, 'rgba(180,210,245,0)');
  ctx.fillStyle = neb;
  ctx.beginPath();
  ctx.arc(x, y, R * 2.6, 0, Math.PI * 2);
  ctx.fill();

  // Dark core
  ctx.fillStyle = 'rgba(9,9,11,0.95)';
  ctx.beginPath();
  ctx.arc(x, y, R, 0, Math.PI * 2);
  ctx.fill();

  // Border
  ctx.strokeStyle = rgba([200, 225, 255], 0.6 * Math.min(1, scale + 0.2));
  ctx.lineWidth = 1.3;
  ctx.beginPath();
  ctx.arc(x, y, R, 0, Math.PI * 2);
  ctx.stroke();

  // Orbiting runes
  const n = Math.round(16 * Math.min(1, scale + 0.3));
  ctx.font = `${Math.round(13 * scale)}px "JetBrains Mono", monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2 + now / 6000;
    ctx.fillStyle = rgba([210, 230, 255], 0.62 + 0.25 * Math.sin(now / 700 + i));
    ctx.fillText(
      MIMIR_RUNES[i % MIMIR_RUNES.length] ?? 'ᚠ',
      x + Math.cos(a) * (R + 10 * scale),
      y + Math.sin(a) * (R + 10 * scale),
    );
  }

  // Label
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = rgba([210, 230, 255], scale >= 0.9 ? 0.9 : 0.7);
  ctx.font = `600 ${Math.round(11 * Math.max(0.85, scale))}px Inter, sans-serif`;
  ctx.fillText(label, x, y + R + (scale >= 0.9 ? 42 : 22));
}

// ── Generic nodes ─────────────────────────────────────────────────────────────

export function drawNode(
  ctx: CanvasRenderingContext2D,
  node: TopologyNode,
  pos: NodePosition,
  hovered: boolean,
  zoom: number,
): void {
  if (node.typeId === 'mimir') return; // handled by drawMimir separately
  if (node.typeId === 'realm' || node.typeId === 'cluster' || node.typeId === 'namespace') return;

  if (node.typeId === 'host') {
    drawHost(ctx, node, pos, hovered);
    return;
  }

  if (node.typeId === 'run') {
    const { x, y } = pos;
    const runRadius = Math.max(
      (pos.containerWidth ?? 100) / 2,
      (pos.containerHeight ?? 100) / 2,
      42,
    );

    ctx.save();
    const g = ctx.createRadialGradient(x, y, 0, x, y, runRadius);
    g.addColorStop(0, 'rgba(56,189,248,0.12)');
    g.addColorStop(0.72, 'rgba(30,41,59,0.05)');
    g.addColorStop(1, 'rgba(15,23,42,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, runRadius, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = rgba(C.frost, hovered ? 0.6 : 0.34);
    ctx.lineWidth = hovered ? 1.3 : 1;
    ctx.setLineDash([4, 5]);
    ctx.beginPath();
    ctx.arc(x, y, runRadius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);

    if (shouldDrawLabel(node.typeId, zoom, hovered)) {
      const px = worldFontSize(LABEL_PX.PRIMARY, zoom);
      ctx.fillStyle = rgba(C.ice, 0.82);
      ctx.font = `${hovered ? 600 : 500} ${px}px "JetBrains Mono", monospace`;
      ctx.textAlign = 'center';
      ctx.fillText(humanizeObservatoryText(node.label), x, y - runRadius - px * 0.6);
    }
    ctx.restore();
    return;
  }

  const { x, y } = pos;
  const size = NODE_SIZE[node.typeId] ?? 6;

  drawNodeSwatch(ctx, node.typeId, x, y, size, hovered);

  // Labels resolve with the camera rather than from a fixed type allowlist:
  // the graph is far too dense to label everything at overview zoom.
  if (!shouldDrawLabel(node.typeId, zoom, hovered)) return;

  const tier = labelTier(node.typeId);
  const px = worldFontSize(tier === 'primary' ? LABEL_PX.PRIMARY : LABEL_PX.SECONDARY, zoom);
  const placement = workflowLabelPlacement(node, size);
  ctx.fillStyle = rgba(C.moon, hovered ? 0.95 : 0.75);
  ctx.font = `${hovered ? 600 : 500} ${px}px Inter, sans-serif`;
  ctx.textAlign = placement.align;
  ctx.textBaseline = placement.baseline;
  ctx.fillText(humanizeObservatoryText(node.label), x + placement.dx, y + placement.dy);
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

/**
 * Outline the agent mesh the operator is currently engaging with.
 *
 * Members are scattered across clusters, so the shape has to be derived from
 * their positions rather than read off a container. Nothing is drawn for a
 * mesh with fewer than two placed members.
 */
export function drawAgentMesh(
  ctx: CanvasRenderingContext2D,
  memberPoints: readonly Point[],
  label: string,
  zoom: number,
): void {
  if (memberPoints.length < 2) return;

  const origin = centroid(memberPoints);
  const outline = expandFromCentroid(convexHull(memberPoints), origin, MESH_HULL.PADDING);

  ctx.save();
  ctx.beginPath();
  if (outline.length < 3) {
    // Two members: a capsule reads better than a degenerate polygon.
    const [a, b] = outline as [Point, Point];
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
  } else {
    // Trace midpoint-to-midpoint with each vertex as the control point, which
    // rounds the hull without needing a corner-by-corner arc solve.
    const last = outline[outline.length - 1]!;
    const first = outline[0]!;
    ctx.moveTo((first.x + last.x) / 2, (first.y + last.y) / 2);
    for (let i = 0; i < outline.length; i++) {
      const current = outline[i]!;
      const next = outline[(i + 1) % outline.length]!;
      ctx.quadraticCurveTo(
        current.x,
        current.y,
        (current.x + next.x) / 2,
        (current.y + next.y) / 2,
      );
    }
    ctx.closePath();
    ctx.fillStyle = rgba(C.valk, MESH_HULL.FILL_ALPHA);
    ctx.fill();
  }

  ctx.setLineDash(MESH_HULL.DASH.map((d) => d));
  ctx.strokeStyle = rgba(C.valk, MESH_HULL.STROKE_ALPHA);
  ctx.lineWidth = worldFontSize(1.4, zoom);
  ctx.stroke();
  ctx.setLineDash([]);

  if (label) {
    const px = worldFontSize(LABEL_PX.PRIMARY, zoom);
    ctx.fillStyle = rgba(C.valk, 0.7);
    ctx.font = `600 ${px}px "JetBrains Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.fillText(humanizeObservatoryText(label).toUpperCase(), origin.x, origin.y);
  }
  ctx.restore();
}
