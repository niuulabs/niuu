/**
 * Canvas 2D drawing helpers.
 *
 * All functions are pure with respect to their arguments — they only mutate
 * the canvas context they receive.  No React or state imports.
 */

import { curveBundle, curveCatmullRom, curveLinear, line } from 'd3-shape';
import { SERVICE_RUNES } from '@niuulabs/ui';
import type { Topology, TopologyNode, TopologyEdge, EdgeKind } from '../../domain';
import { humanizeObservatoryText } from '../displayLabels';
import type { NodePosition } from './layoutEngine';
import { zoneRadius, HOST_HALF_W, HOST_HALF_H } from './layoutEngine';
import { NODE_SIZE, MIMIR_RUNES, LAYOUT } from './config';

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

function rgba([r, g, b]: readonly [number, number, number], a: number): string {
  return `rgba(${r},${g},${b},${a})`;
}

function nodeColour(typeId: string): readonly [number, number, number] {
  switch (typeId) {
    case 'ting':
    case 'ravn_run':
      return C.frost;
    case 'bifrost':
    case 'skuld':
      return C.indigo;
    case 'volundr':
    case 'ravn_long':
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

function identityRune(typeId: string): string {
  const direct = SERVICE_RUNES[typeId as keyof typeof SERVICE_RUNES];
  if (direct) return direct;

  const alias: Partial<Record<string, keyof typeof SERVICE_RUNES>> = {
    ravn_long: 'ravn',
    ravn_run: 'ravn',
  };
  const key = alias[typeId];
  return key ? SERVICE_RUNES[key] : '';
}

type ServiceBadgeKind = 'rounded' | 'hex' | 'diamond' | 'pentagon';

function drawServiceBadge(
  ctx: CanvasRenderingContext2D,
  kind: ServiceBadgeKind,
  cx: number,
  cy: number,
  size: number,
  col: readonly [number, number, number],
): void {
  ctx.fillStyle = rgba(col, 0.13);
  ctx.strokeStyle = rgba(col, 0.92);
  ctx.lineWidth = 1.1;
  ctx.beginPath();
  switch (kind) {
    case 'rounded':
      ctx.roundRect(cx - size * 1.04, cy - size * 1.04, size * 2.08, size * 2.08, size * 0.38);
      break;
    case 'hex':
      ctx.moveTo(cx - size * 0.72, cy - size * 0.46);
      ctx.lineTo(cx, cy - size * 0.84);
      ctx.lineTo(cx + size * 0.72, cy - size * 0.46);
      ctx.lineTo(cx + size * 0.72, cy + size * 0.46);
      ctx.lineTo(cx, cy + size * 0.84);
      ctx.lineTo(cx - size * 0.72, cy + size * 0.46);
      ctx.closePath();
      break;
    case 'diamond':
      ctx.moveTo(cx, cy - size * 0.96);
      ctx.lineTo(cx + size * 0.9, cy);
      ctx.lineTo(cx, cy + size * 0.96);
      ctx.lineTo(cx - size * 0.9, cy);
      ctx.closePath();
      break;
    case 'pentagon':
      ctx.moveTo(cx, cy - size * 0.96);
      ctx.lineTo(cx + size * 0.86, cy - size * 0.18);
      ctx.lineTo(cx + size * 0.54, cy + size * 0.86);
      ctx.lineTo(cx - size * 0.54, cy + size * 0.86);
      ctx.lineTo(cx - size * 0.86, cy - size * 0.18);
      ctx.closePath();
      break;
  }
  ctx.fill();
  ctx.stroke();
}

function workflowLabelPlacement(
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

function structureLabel(node: TopologyNode): string {
  return humanizeObservatoryText(node.label);
}

function drawStructureGlyph(
  ctx: CanvasRenderingContext2D,
  typeId: 'realm' | 'cluster' | 'host',
  x: number,
  y: number,
): void {
  ctx.save();
  switch (typeId) {
    case 'realm': {
      ctx.strokeStyle = rgba(C.indigo, 0.62);
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.arc(x, y, 5.7, Math.PI * 0.18, Math.PI * 1.82);
      ctx.stroke();
      ctx.strokeStyle = rgba(C.ice, 0.7);
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.arc(x + 1.2, y - 0.8, 3.1, Math.PI * 0.28, Math.PI * 1.78);
      ctx.stroke();
      ctx.fillStyle = rgba(C.frost, 0.9);
      ctx.beginPath();
      ctx.arc(x - 1.6, y + 0.9, 1.15, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = rgba(C.indigo, 0.9);
      ctx.beginPath();
      ctx.arc(x + 4.9, y - 2.3, 1.1, 0, Math.PI * 2);
      ctx.fill();
      break;
    }
    case 'cluster': {
      const points = [
        { x, y: y - 4.5 },
        { x: x - 4.5, y: y + 3.5 },
        { x: x + 4.5, y: y + 3.5 },
      ];
      ctx.strokeStyle = rgba(C.indigo, 0.68);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(points[0]!.x, points[0]!.y);
      ctx.lineTo(points[1]!.x, points[1]!.y);
      ctx.lineTo(points[2]!.x, points[2]!.y);
      ctx.closePath();
      ctx.stroke();
      ctx.fillStyle = rgba(C.ice, 0.86);
      for (const point of points) {
        ctx.beginPath();
        ctx.arc(point.x, point.y, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
      break;
    }
    case 'host': {
      ctx.strokeStyle = rgba(C.moon, 0.76);
      ctx.lineWidth = 0.9;
      ctx.beginPath();
      ctx.arc(x, y, 4.8, Math.PI * 0.16, Math.PI * 1.88);
      ctx.stroke();
      ctx.strokeStyle = rgba(C.moon, 0.68);
      ctx.lineWidth = 0.85;
      ctx.beginPath();
      ctx.moveTo(x - 2.8, y - 1.8);
      ctx.lineTo(x + 1.9, y - 1.8);
      ctx.moveTo(x - 2.8, y + 0.2);
      ctx.lineTo(x + 1.9, y + 0.2);
      ctx.moveTo(x - 2.8, y + 2.2);
      ctx.lineTo(x + 1.9, y + 2.2);
      ctx.stroke();
      ctx.fillStyle = rgba(C.frost, 0.9);
      ctx.beginPath();
      ctx.arc(x + 2.9, y - 1.8, 0.72, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x + 2.9, y + 0.2, 0.72, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x + 2.9, y + 2.2, 0.72, 0, Math.PI * 2);
      ctx.fill();
      break;
    }
  }
  ctx.restore();
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
  const label = uppercase ? structureLabel(node).toUpperCase() : structureLabel(node);
  const metrics = ctx.measureText?.(label);
  const textWidth = metrics?.width ?? label.length * 7.2;
  const glyphGap = 8;
  const glyphWidth = 12;
  const startX = x - (textWidth + glyphGap + glyphWidth) / 2;
  const glyphX = startX + glyphWidth / 2;
  const textX = startX + glyphWidth + glyphGap;

  ctx.save();
  drawStructureGlyph(ctx, node.typeId as 'realm' | 'cluster' | 'host', glyphX, y - 4);
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
  const glyphWidth = 12;
  const charWidth = node.typeId === 'realm' ? 7.8 : 7.2;
  const textWidth = Math.max(label.length * charWidth, 18);
  const totalWidth = glyphWidth + glyphGap + textWidth;
  const radius =
    node.typeId === 'realm' || node.typeId === 'cluster'
      ? pos.zoneRadius ?? zoneRadius(node.typeId)
      : Math.max((pos.containerWidth ?? HOST_HALF_W * 2) / 2, (pos.containerHeight ?? HOST_HALF_H * 2) / 2);
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
): void {
  // Draw realms first (larger), then clusters on top.
  for (const typeId of ['realm', 'cluster'] as const) {
    for (const node of nodes) {
      if (node.typeId !== typeId) continue;
      const pos = positions.get(node.id);
      if (!pos) continue;
      const r = pos.zoneRadius ?? zoneRadius(typeId);
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

        drawStructureLabel(ctx, node, cx, cy - r - 8, {
          font: '600 13px Inter, sans-serif',
          color: rgba(C.ice, 0.78),
          uppercase: true,
        });
      } else {
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

        drawStructureLabel(ctx, node, cx, cy - r - 4, {
          font: '10px "JetBrains Mono", monospace',
          color: rgba(C.ice, 0.58),
        });
      }
    }
  }
}

// ── Edges (5 kinds) ───────────────────────────────────────────────────────────

function edgeHash(id: string): number {
  let hash = 5381;
  for (let index = 0; index < id.length; index += 1) {
    hash = (((hash << 5) + hash) ^ id.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function nodeEdgeRadius(node: TopologyNode | undefined): number {
  if (!node) return 8;
  if (node.typeId === 'mimir') return LAYOUT.MIMIR_RADIUS;
  if (node.typeId === 'host') return Math.max(HOST_HALF_W, HOST_HALF_H);
  if (node.typeId === 'run') return 50;
  return (NODE_SIZE[node.typeId] ?? 6) + 3;
}

function trimToNodeBoundary(
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

function parentChain(
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

function sharedAncestor(
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

function bundleWaypoint(
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

function edgeProfile(kind: EdgeKind | string | undefined, now: number) {
  switch (kind) {
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

function drawEdge(
  ctx: CanvasRenderingContext2D,
  edge: TopologyEdge,
  nodeById: Map<string, TopologyNode>,
  positions: Map<string, NodePosition>,
  now: number,
): void {
  const src = positions.get(edge.sourceId);
  const dst = positions.get(edge.targetId);
  if (!src || !dst) return;
  const srcNode = nodeById.get(edge.sourceId);
  const dstNode = nodeById.get(edge.targetId);
  const start = trimToNodeBoundary(srcNode, src, dst);
  const end = trimToNodeBoundary(dstNode, dst, src);
  const profile = edgeProfile(edge.kind, now);

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
  const sameParent =
    srcNode?.parentId != null &&
    srcNode.parentId === dstNode?.parentId &&
    srcNode.parentId !== null;
  const sameRunFlow = sameParent && sharedParentNode?.typeId === 'run';
  const sameClusterSoft =
    sameParent &&
    sharedParentNode?.typeId === 'cluster' &&
    (edge.kind === 'soft' || edge.kind === 'solid');
  const directParentChild = srcNode?.id === dstNode?.parentId || dstNode?.id === srcNode?.parentId;
  const offset = Math.min(
    profile.bend *
      (sameRunFlow ? 0.32 : sameClusterSoft ? 0.18 : sameParent ? 1.2 : 1) *
      (directParentChild ? 0.7 : 1),
    length * 0.28,
  );
  const midX = (start.x + end.x) / 2;
  const midY = (start.y + end.y) / 2;
  let cx = midX + nx * offset * sign;
  let cy = midY + ny * offset * sign;

  if (ancestorNode && !sameRunFlow && !sameClusterSoft) {
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

  const edgeLine = line<{ x: number; y: number }>()
    .x((point) => point.x)
    .y((point) => point.y)
    .curve(
      sameRunFlow
        ? curveLinear
        : sameClusterSoft
        ? curveCatmullRom.alpha(0.5)
        : ancestorNode && !directParentChild && !sameRunFlow
        ? curveBundle.beta(profile.bundleStrength)
        : curveCatmullRom.alpha(0.72),
    )
    .context(ctx);
  const points: Array<{ x: number; y: number }> = [start];

  const ancestorPos = ancestorNode ? positions.get(ancestorNode.id) : undefined;
  if (sameRunFlow) {
    points.push(end);
  } else if (sameClusterSoft) {
    points.push(
      {
        x: start.x + (end.x - start.x) * 0.34,
        y: start.y + (end.y - start.y) * 0.24,
      },
      {
        x: start.x + (end.x - start.x) * 0.68,
        y: start.y + (end.y - start.y) * 0.76,
      },
    );
    points.push(end);
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

  if (sameParent && !directParentChild && ancestorPos && !sameRunFlow) {
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

  if (edge.label && !sameRunFlow) {
    const labelX = ancestorPos && !directParentChild && !sameRunFlow ? cx : midX;
    const labelY = ancestorPos && !directParentChild && !sameRunFlow ? cy : midY;
    ctx.font = '500 9px "JetBrains Mono", monospace';
    const metrics = ctx.measureText(edge.label);
    const width = Math.max(metrics.width + 10, 30);
    const height = 16;
    ctx.fillStyle = 'rgba(9,9,11,0.76)';
    ctx.beginPath();
    ctx.roundRect(labelX - width / 2, labelY - height / 2, width, height, 5);
    ctx.fill();
    ctx.strokeStyle = rgba(C.ice, 0.12);
    ctx.lineWidth = 0.8;
    ctx.stroke();
    ctx.fillStyle = rgba(C.ice, 0.72);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(edge.label, labelX, labelY + 0.5);
    ctx.textBaseline = 'alphabetic';
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
  for (const edge of topology.edges) {
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

function drawShape(
  ctx: CanvasRenderingContext2D,
  typeId: string,
  cx: number,
  cy: number,
  size: number,
  col: readonly [number, number, number],
): void {
  switch (typeId) {
    case 'ting':
      drawServiceBadge(ctx, 'rounded', cx, cy, size, col);
      return;

    case 'volundr':
      drawServiceBadge(ctx, 'hex', cx, cy, size, col);
      return;

    case 'bifrost': {
      drawServiceBadge(ctx, 'diamond', cx, cy, size, col);
      return;
    }

    case 'ravn_long':
      drawServiceBadge(ctx, 'diamond', cx, cy, size, col);
      return;

    case 'ravn_run':
      drawServiceBadge(ctx, 'diamond', cx, cy, size, col);
      return;

    case 'trigger':
      ctx.strokeStyle = rgba(col, 0.92);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(cx, cy, size, 0, Math.PI * 2);
      ctx.stroke();
      ctx.fillStyle = rgba(col, 0.78);
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(2.6, size * 0.38), 0, Math.PI * 2);
      ctx.fill();
      return;

    case 'end':
      ctx.strokeStyle = rgba(col, 0.92);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(cx, cy, size, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(2.6, size - 3), 0, Math.PI * 2);
      ctx.stroke();
      return;

    case 'stage':
      ctx.fillStyle = rgba(col, 0.18);
      ctx.strokeStyle = rgba(col, 0.88);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.roundRect(cx - size * 1.45, cy - size * 0.92, size * 2.9, size * 1.84, size * 0.7);
      ctx.fill();
      ctx.stroke();
      return;

    case 'gate':
      ctx.fillStyle = 'rgba(9,9,11,0.7)';
      ctx.strokeStyle = rgba(col, 0.9);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(cx, cy - size);
      ctx.lineTo(cx + size, cy);
      ctx.lineTo(cx, cy + size);
      ctx.lineTo(cx - size, cy);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      return;

    case 'cond':
      ctx.strokeStyle = rgba(col, 0.9);
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(cx, cy - size);
      ctx.lineTo(cx + size, cy);
      ctx.lineTo(cx, cy + size);
      ctx.lineTo(cx - size, cy);
      ctx.closePath();
      ctx.stroke();
      ctx.fillStyle = rgba(col, 0.18);
      ctx.fill();
      return;

    case 'resource':
      ctx.fillStyle = rgba(col, 0.14);
      ctx.strokeStyle = rgba(col, 0.88);
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.moveTo(cx - size * 0.85, cy - size);
      ctx.lineTo(cx + size * 0.3, cy - size);
      ctx.lineTo(cx + size, cy - size * 0.3);
      ctx.lineTo(cx + size, cy + size);
      ctx.lineTo(cx - size * 0.85, cy + size);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      return;

    case 'skuld':
      drawServiceBadge(ctx, 'hex', cx, cy, size, col);
      return;

    case 'valkyrie':
      drawServiceBadge(ctx, 'pentagon', cx, cy, size, col);
      return;

    case 'beacon':
      ctx.strokeStyle = rgba(col, 0.6);
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 2]);
      ctx.beginPath();
      ctx.arc(cx, cy, size, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = rgba(col, 0.6);
      ctx.beginPath();
      ctx.arc(cx, cy, 2, 0, Math.PI * 2);
      ctx.fill();
      return;

    case 'printer':
    case 'vaettir':
      ctx.strokeStyle = rgba(col, 0.9);
      ctx.lineWidth = 1.3;
      ctx.strokeRect(cx - size, cy - size, size * 2, size * 2);
      ctx.fillStyle = rgba(col, 0.25);
      ctx.fillRect(cx - size, cy - size, size * 2, size * 2);
      return;

    default:
      // service, model, run, …
      ctx.fillStyle = rgba(col, 0.85);
      ctx.beginPath();
      ctx.arc(cx, cy, size, 0, Math.PI * 2);
      ctx.fill();
      return;
  }
}

export function drawNode(
  ctx: CanvasRenderingContext2D,
  node: TopologyNode,
  pos: NodePosition,
  hovered: boolean,
): void {
  if (node.typeId === 'mimir') return; // handled by drawMimir separately
  if (node.typeId === 'realm' || node.typeId === 'cluster') return;

  if (node.typeId === 'host') {
    drawHost(ctx, node, pos, hovered);
    return;
  }

  if (node.typeId === 'run') {
    const { x, y } = pos;
    const runRadius = Math.max((pos.containerWidth ?? 100) / 2, (pos.containerHeight ?? 100) / 2, 42);

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

    ctx.fillStyle = rgba(C.ice, 0.82);
    ctx.font = `${hovered ? 600 : 500} 10px "JetBrains Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.fillText(humanizeObservatoryText(node.label), x, y - runRadius - 8);
    ctx.restore();
    return;
  }

  const { x, y } = pos;
  const size = NODE_SIZE[node.typeId] ?? 6;
  const col = nodeColour(node.typeId);

  // Hover ring
  if (hovered) {
    ctx.strokeStyle = rgba(C.moon, 0.8);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, size + 5, 0, Math.PI * 2);
    ctx.stroke();
  }

  ctx.save();
  drawShape(ctx, node.typeId, x, y, size, col);
  ctx.restore();

  // Identity rune for primary coordinators
  const rune = identityRune(node.typeId);
  if (rune) {
    ctx.save();
    ctx.fillStyle = rgba(C.moon, 0.96);
    ctx.font = '700 11px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(rune, x, y + 1);
    ctx.textBaseline = 'alphabetic';
    ctx.restore();
  }

  // Label below node for key types and hovered nodes
  const showLabel =
    [
      'ting',
      'bifrost',
      'volundr',
      'valkyrie',
      'ravn_long',
      'trigger',
      'stage',
      'gate',
      'cond',
      'resource',
      'end',
    ].includes(node.typeId) ||
    (node.typeId === 'service' && ['observatory', 'niuu', 'ravn'].includes(node.svcType ?? '')) ||
    hovered;
  if (showLabel) {
    const placement = workflowLabelPlacement(node, size);
    ctx.fillStyle = rgba(C.moon, hovered ? 0.95 : 0.75);
    ctx.font = `${hovered ? 600 : 500} 10px Inter, sans-serif`;
    ctx.textAlign = placement.align;
    ctx.textBaseline = placement.baseline;
    ctx.fillText(humanizeObservatoryText(node.label), x + placement.dx, y + placement.dy);
    ctx.textBaseline = 'alphabetic';
  }
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
