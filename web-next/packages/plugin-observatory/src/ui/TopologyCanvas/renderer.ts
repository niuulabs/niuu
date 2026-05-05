/**
 * Canvas 2D drawing helpers.
 *
 * All functions are pure with respect to their arguments — they only mutate
 * the canvas context they receive.  No React or state imports.
 */

import type { Topology, TopologyNode, TopologyEdge, EdgeKind } from '../../domain';
import type { NodePosition } from './layoutEngine';
import { zoneRadius, HOST_HALF_W, HOST_HALF_H } from './layoutEngine';
import { NODE_SIZE, MIMIR_RUNES, LAYOUT } from './config';

// ── Colour palette ────────────────────────────────────────────────────────────
// These map directly to the ice-theme brand ramp used in the prototype.
const C = {
  ice: [186, 230, 253] as const, // brand-300
  frost: [125, 211, 252] as const, // active / raid
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
    case 'tyr':
    case 'ravn_raid':
      return C.frost;
    case 'bifrost':
    case 'skuld':
      return C.indigo;
    case 'volundr':
    case 'ravn_long':
    case 'mimir':
    case 'mimir_sub':
      return C.moon;
    case 'valkyrie':
      return C.valk;
    case 'model':
      return C.model;
    case 'service':
    case 'raid':
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
  const map: Record<string, string> = {
    tyr: 'ᛃ',
    bifrost: 'ᚨ',
    volundr: 'ᚲ',
    mimir: 'ᛗ',
    mimir_sub: 'ᛗ',
  };
  return map[typeId] ?? '';
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
      const r = zoneRadius(typeId);
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

        ctx.fillStyle = rgba(C.ice, 0.78);
        ctx.font = '600 13px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(node.label.toUpperCase(), cx, cy - r - 8);
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

        ctx.fillStyle = rgba(C.ice, 0.58);
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`⎔ ${node.label}`, cx, cy - r - 4);
      }
    }
  }
}

// ── Edges (5 kinds) ───────────────────────────────────────────────────────────

function drawEdge(
  ctx: CanvasRenderingContext2D,
  edge: TopologyEdge,
  positions: Map<string, NodePosition>,
  now: number,
): void {
  const src = positions.get(edge.sourceId);
  const dst = positions.get(edge.targetId);
  if (!src || !dst) return;

  ctx.save();
  ctx.lineCap = 'round';

  const kind: EdgeKind = edge.kind;

  switch (kind) {
    case 'solid':
      // Solid cyan — Týr → Völundr coordinator links.
      ctx.strokeStyle = rgba(C.indigo, 0.42);
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      break;

    case 'dashed-anim':
      // Animated dashes — Týr → raid dispatch channel.
      ctx.strokeStyle = rgba(C.frost, 0.48);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 5]);
      ctx.lineDashOffset = -now / 80;
      break;

    case 'dashed-long':
      // Long dashes — raven async memory access to Mímir.
      ctx.strokeStyle = rgba(C.moon, 0.32);
      ctx.lineWidth = 0.9;
      ctx.setLineDash([6, 4]);
      ctx.lineDashOffset = -now / 120;
      break;

    case 'soft':
      // Soft translucent — Bifröst soft reference to Mímir.
      ctx.strokeStyle = rgba(C.moon, 0.18);
      ctx.lineWidth = 0.8;
      ctx.setLineDash([]);
      break;

    case 'raid':
      // Frost — inter-raven cohesion within a raid.
      ctx.strokeStyle = rgba(C.frost, 0.38);
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      break;
  }

  ctx.beginPath();
  ctx.moveTo(src.x, src.y);
  ctx.lineTo(dst.x, dst.y);
  ctx.stroke();
  ctx.restore();
}

export function drawEdges(
  ctx: CanvasRenderingContext2D,
  topology: Topology,
  positions: Map<string, NodePosition>,
  now: number,
): void {
  ctx.save();
  for (const edge of topology.edges) {
    drawEdge(ctx, edge, positions, now);
  }
  ctx.restore();
}

// ── Hosts ─────────────────────────────────────────────────────────────────────

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawHost(
  ctx: CanvasRenderingContext2D,
  node: TopologyNode,
  pos: NodePosition,
  hovered: boolean,
): void {
  const hw = HOST_HALF_W;
  const hh = HOST_HALF_H;
  const x = pos.x - hw;
  const y = pos.y - hh;

  ctx.save();
  ctx.fillStyle = hovered ? 'rgba(45,55,78,0.6)' : 'rgba(27,35,54,0.42)';
  drawRoundedRect(ctx, x, y, hw * 2, hh * 2, 8);
  ctx.fill();

  ctx.strokeStyle = hovered ? rgba(C.ice, 0.55) : rgba(C.slate, 0.35);
  ctx.lineWidth = hovered ? 1.4 : 1;
  drawRoundedRect(ctx, x, y, hw * 2, hh * 2, 8);
  ctx.stroke();

  ctx.fillStyle = rgba(C.moon, 0.78);
  ctx.font = '600 10px Inter, sans-serif';
  ctx.textAlign = 'left';
  ctx.fillText(node.label, x + 8, y + 14);

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
  const a = rgba(col, 0.92);

  switch (typeId) {
    case 'tyr':
    case 'volundr':
      ctx.fillStyle = a;
      ctx.fillRect(cx - size, cy - size, size * 2, size * 2);
      return;

    case 'bifrost': {
      ctx.fillStyle = a;
      ctx.beginPath();
      for (let k = 0; k < 5; k++) {
        const ang = -Math.PI / 2 + (k / 5) * Math.PI * 2;
        const px = cx + Math.cos(ang) * size;
        const py = cy + Math.sin(ang) * size;
        if (k === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
      return;
    }

    case 'ravn_long':
      ctx.fillStyle = rgba(col, 0.95);
      ctx.beginPath();
      ctx.moveTo(cx, cy - size);
      ctx.lineTo(cx + size, cy);
      ctx.lineTo(cx, cy + size);
      ctx.lineTo(cx - size, cy);
      ctx.closePath();
      ctx.fill();
      return;

    case 'ravn_raid':
      ctx.fillStyle = rgba(col, 0.9);
      ctx.beginPath();
      ctx.moveTo(cx, cy - size);
      ctx.lineTo(cx + size, cy + size * 0.7);
      ctx.lineTo(cx - size, cy + size * 0.7);
      ctx.closePath();
      ctx.fill();
      return;

    case 'skuld':
      ctx.fillStyle = 'rgba(9,9,11,0.8)';
      ctx.strokeStyle = rgba(col, 0.9);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      for (let k = 0; k < 6; k++) {
        const ang = -Math.PI / 2 + (k / 6) * Math.PI * 2;
        const px = cx + Math.cos(ang) * size;
        const py = cy + Math.sin(ang) * size;
        if (k === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      return;

    case 'valkyrie':
      ctx.fillStyle = rgba(col, 0.95);
      ctx.beginPath();
      ctx.moveTo(cx - size, cy + size * 0.6);
      ctx.lineTo(cx, cy - size);
      ctx.lineTo(cx + size, cy + size * 0.6);
      ctx.lineTo(cx, cy + size * 0.25);
      ctx.closePath();
      ctx.fill();
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

    case 'mimir_sub':
      // Small Mímir: dark circle with rune
      ctx.fillStyle = 'rgba(9,9,11,0.95)';
      ctx.beginPath();
      ctx.arc(cx, cy, size, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = rgba(C.moon, 0.55);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, size, 0, Math.PI * 2);
      ctx.stroke();
      ctx.fillStyle = rgba(C.moon, 0.8);
      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('ᛗ', cx, cy + 1);
      ctx.textBaseline = 'alphabetic';
      return;

    default:
      // service, model, raid, …
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

  if (node.typeId === 'host') {
    drawHost(ctx, node, pos, hovered);
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
    ctx.fillStyle = 'rgba(9,9,11,0.88)';
    ctx.font = '700 11px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(rune, x, y + 1);
    ctx.textBaseline = 'alphabetic';
    ctx.restore();
  }

  // Label below node for key types and hovered nodes
  const showLabel =
    ['tyr', 'bifrost', 'volundr', 'valkyrie', 'ravn_long'].includes(node.typeId) || hovered;
  if (showLabel) {
    ctx.fillStyle = rgba(C.moon, hovered ? 0.95 : 0.75);
    ctx.font = `${hovered ? 600 : 500} 10px Inter, sans-serif`;
    ctx.textAlign = 'center';
    ctx.fillText(node.label, x, y + size + 13);
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
