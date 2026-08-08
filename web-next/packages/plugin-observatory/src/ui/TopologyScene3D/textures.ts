/**
 * Canvas-drawn textures for the sprite work: glows, pulse rings, flow motes
 * and text.
 *
 * All of them are drawn white and tinted by the sprite material, so one glow
 * serves every compute hue and every health colour instead of one texture per
 * combination.
 *
 * Every factory returns `null` when there is no usable 2D context. That is not
 * defensive padding — the scene has to build in environments that have no
 * canvas at all (server rendering, headless test runs), and a scene missing
 * its glows is still a scene, whereas one that throws while building is
 * nothing.
 */

import { CanvasTexture, LinearFilter, SRGBColorSpace, type Texture } from 'three';

/** Edge of the largest sprite texture, in pixels. */
const GLOW_SIZE = 128;
const RING_SIZE = 256;
const MOTE_SIZE = 64;

/** Text is rasterised at this pixel height and scaled down in world units. */
const LABEL_FONT_PX = 44;
const LABEL_PADDING_PX = 12;

type Ctx = CanvasRenderingContext2D;

/**
 * A canvas and its context, or null when this environment cannot draw.
 *
 * Each factory names the calls it is about to make and they are probed up
 * front. Assuming them instead only moves the discovery: a partial context —
 * which is what headless runners and server renderers supply — throws halfway
 * through building a texture, and a half-built texture takes the whole scene
 * down with it.
 */
function makeCanvas(
  width: number,
  height: number,
  requires: readonly (keyof Ctx)[],
): { canvas: HTMLCanvasElement; ctx: Ctx } | null {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.ceil(width));
  canvas.height = Math.max(1, Math.ceil(height));
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  for (const method of requires) {
    if (typeof ctx[method] !== 'function') return null;
  }
  return { canvas, ctx };
}

function toTexture(canvas: HTMLCanvasElement): Texture {
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

/**
 * The soft ball of light behind every node.
 *
 * A quartic falloff rather than a linear one: a linear gradient reads as a
 * flat disc with a hard shoulder, which at a hundred nodes looks like fog
 * rather than like each thing being lit.
 */
export function createGlowTexture(): Texture | null {
  const made = makeCanvas(GLOW_SIZE, GLOW_SIZE, ['createImageData', 'putImageData']);
  if (!made) return null;
  const { canvas, ctx } = made;
  const centre = GLOW_SIZE / 2;
  const image = ctx.createImageData(GLOW_SIZE, GLOW_SIZE);
  for (let y = 0; y < GLOW_SIZE; y += 1) {
    for (let x = 0; x < GLOW_SIZE; x += 1) {
      const distance = Math.hypot(x - centre, y - centre) / centre;
      const falloff = Math.max(0, 1 - distance) ** 4;
      const offset = (y * GLOW_SIZE + x) * 4;
      image.data[offset] = 255;
      image.data[offset + 1] = 255;
      image.data[offset + 2] = 255;
      image.data[offset + 3] = Math.round(falloff * 255);
    }
  }
  ctx.putImageData(image, 0, 0);
  return toTexture(canvas);
}

/** The expanding outline a pulsing mesh member wears. */
export function createRingTexture(): Texture | null {
  const made = makeCanvas(RING_SIZE, RING_SIZE, ['beginPath', 'arc', 'stroke']);
  if (!made) return null;
  const { canvas, ctx } = made;
  const centre = RING_SIZE / 2;
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = RING_SIZE * 0.035;
  ctx.beginPath();
  ctx.arc(centre, centre, centre - ctx.lineWidth, 0, Math.PI * 2);
  ctx.stroke();
  return toTexture(canvas);
}

/** One travelling mark on an edge that reports a measured rate. */
export function createMoteTexture(): Texture | null {
  const made = makeCanvas(MOTE_SIZE, MOTE_SIZE, ['createRadialGradient', 'fillRect']);
  if (!made) return null;
  const { canvas, ctx } = made;
  const centre = MOTE_SIZE / 2;
  const gradient = ctx.createRadialGradient(centre, centre, 0, centre, centre, centre);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.35, 'rgba(255,255,255,0.85)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, MOTE_SIZE, MOTE_SIZE);
  return toTexture(canvas);
}

export interface LabelTexture {
  texture: Texture;
  /** Width divided by height, so the sprite can be scaled without stretching. */
  aspect: number;
}

/**
 * How wide a name will be, relative to its height, before it is rasterised.
 *
 * The layout has to know how much room a name wants in order to decide whether
 * to draw it at all — and rasterising every candidate just to measure it would
 * mean building the very textures the layout is about to reject.
 */
export function estimateLabelAspect(text: string): number {
  return (text.length * LABEL_FONT_PX * 0.6 + LABEL_PADDING_PX * 2) / (LABEL_FONT_PX * 1.5);
}

/**
 * A line of text as a texture.
 *
 * Drawn with a dark stroke under the fill: labels float over arcs, plates and
 * other labels, and white-on-nothing loses its edges the moment it crosses a
 * lit region.
 */
export function createLabelTexture(
  text: string,
  { weight = 500 }: { weight?: number } = {},
): LabelTexture | null {
  if (!text) return null;
  const probe = makeCanvas(1, 1, ['measureText', 'fillText', 'strokeText']);
  if (!probe) return null;

  const font = `${weight} ${LABEL_FONT_PX}px "JetBrains Mono", monospace`;
  probe.ctx.font = font;
  const measured = probe.ctx.measureText(text).width;
  // A stubbed context reports zero for everything; fall back to a monospace
  // estimate rather than producing a one-pixel-wide sprite.
  const textWidth = measured > 0 ? measured : text.length * LABEL_FONT_PX * 0.6;
  const width = Math.ceil(textWidth + LABEL_PADDING_PX * 2);
  const height = Math.ceil(LABEL_FONT_PX * 1.5);

  const made = makeCanvas(width, height, ['measureText', 'fillText', 'strokeText']);
  if (!made) return null;
  const { canvas, ctx } = made;

  ctx.font = font;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineJoin = 'round';
  ctx.lineWidth = 6;
  ctx.strokeStyle = 'rgba(3,6,12,0.92)';
  ctx.strokeText(text, width / 2, height / 2);
  ctx.fillStyle = '#ffffff';
  ctx.fillText(text, width / 2, height / 2);

  return { texture: toTexture(canvas), aspect: width / height };
}

// ── Node marks ────────────────────────────────────────────────────────────────

/** Edge of a node mark's texture, in pixels. */
const MARK_SIZE = 64;

/**
 * The small mark a node is drawn as.
 *
 * Quiet on purpose — the instrument work belongs to the regions, and a stage
 * where every one of eighty entities is also an instrument is a stage nobody
 * can read. But quiet is not anonymous: the mark still follows the registry's
 * shape for that type, so a model still reads as a model at a glance, at a
 * fraction of the ink a solid body cost.
 *
 * Drawn white and tinted by the sprite, so one mark per shape serves every
 * compute hue and every health colour.
 */
export function createMarkTexture(shape: string): Texture | null {
  const made = makeCanvas(MARK_SIZE, MARK_SIZE, ['beginPath', 'moveTo', 'lineTo', 'fill', 'arc']);
  if (!made) return null;
  const { canvas, ctx } = made;
  const c = MARK_SIZE / 2;
  // Room for the stroke, so no mark is clipped by its own texture edge.
  const r = c * 0.62;

  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = MARK_SIZE * 0.075;
  ctx.lineJoin = 'round';

  /** A regular polygon, point-up. */
  const polygon = (sides: number, rotation: number, filled: boolean): void => {
    ctx.beginPath();
    for (let i = 0; i < sides; i += 1) {
      const angle = rotation + (i / sides) * Math.PI * 2;
      const x = c + Math.cos(angle) * r;
      const y = c + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    if (filled) ctx.fill();
    else ctx.stroke();
  };

  switch (shape) {
    case 'agent':
    case 'halo':
      // A resident: a filled core inside its own ring, the busiest mark in the
      // vocabulary because it is the thing an operator is usually looking for.
      ctx.beginPath();
      ctx.arc(c, c, r * 0.46, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(c, c, r, 0, Math.PI * 2);
      ctx.stroke();
      break;
    case 'triangle':
      polygon(3, -Math.PI / 2, true);
      break;
    case 'rack':
      // Wide and low, the way a rack is.
      ctx.fillRect(c - r, c - r * 0.5, r * 2, r);
      break;
    case 'pentagon':
      polygon(5, -Math.PI / 2, true);
      break;
    case 'hex':
      polygon(6, 0, true);
      break;
    case 'hex-flat':
      polygon(6, 0, false);
      break;
    case 'cylinder':
    case 'printer':
      ctx.fillRect(c - r * 0.72, c - r, r * 1.44, r * 2);
      break;
    case 'beacon':
      polygon(3, -Math.PI / 2, false);
      break;
    case 'ring':
    case 'ring-dashed':
      ctx.beginPath();
      ctx.arc(c, c, r, 0, Math.PI * 2);
      ctx.stroke();
      break;
    case 'square-sm':
    case 'box':
      ctx.fillRect(c - r * 0.8, c - r * 0.8, r * 1.6, r * 1.6);
      break;
    default:
      ctx.beginPath();
      ctx.arc(c, c, r * 0.8, 0, Math.PI * 2);
      ctx.fill();
      break;
  }

  return toTexture(canvas);
}

// ── Region readouts ───────────────────────────────────────────────────────────

/** Type height and layout of a readout panel, in texture pixels. */
const READOUT_FONT_PX = 26;
const READOUT_TITLE_PX = 30;
const READOUT_LINE_PX = 38;
const READOUT_PAD_PX = 20;
const READOUT_WIDTH_PX = 300;

export interface ReadoutPanel {
  texture: Texture;
  /** Width divided by height, so the panel scales without stretching. */
  aspect: number;
}

/**
 * A region's figures, as a panel.
 *
 * Label left, value right, in the mono face the rest of the Observatory reads
 * in — the layout of a readout, not a caption. Rows arrive already filtered to
 * what the snapshot holds, so nothing here has to decide what a missing figure
 * looks like.
 */
export function createReadoutTexture(
  title: string,
  rows: ReadonlyArray<{ label: string; value: string }>,
): ReadoutPanel | null {
  const height = READOUT_PAD_PX * 2 + READOUT_LINE_PX * (rows.length + 1);
  const made = makeCanvas(READOUT_WIDTH_PX, height, ['fillText', 'fillRect', 'measureText']);
  if (!made) return null;
  const { canvas, ctx } = made;

  const left = READOUT_PAD_PX;
  const right = READOUT_WIDTH_PX - READOUT_PAD_PX;
  let y = READOUT_PAD_PX + READOUT_TITLE_PX;

  ctx.textBaseline = 'alphabetic';
  ctx.font = `600 ${READOUT_TITLE_PX}px "JetBrains Mono", monospace`;
  ctx.textAlign = 'left';
  ctx.fillStyle = '#ffffff';
  ctx.fillText(title.toUpperCase(), left, y);

  // A rule under the title, so the panel reads as one block rather than as a
  // caption with loose text under it.
  y += READOUT_LINE_PX * 0.42;
  ctx.fillStyle = 'rgba(255,255,255,0.32)';
  ctx.fillRect(left, y, right - left, 2);

  ctx.font = `${READOUT_FONT_PX}px "JetBrains Mono", monospace`;
  for (const row of rows) {
    y += READOUT_LINE_PX;
    ctx.textAlign = 'left';
    ctx.fillStyle = 'rgba(255,255,255,0.62)';
    ctx.fillText(row.label, left, y);
    ctx.textAlign = 'right';
    ctx.fillStyle = '#ffffff';
    ctx.fillText(row.value, right, y);
  }

  return { texture: toTexture(canvas), aspect: READOUT_WIDTH_PX / height };
}
