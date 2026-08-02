/**
 * The glyph vocabulary.
 *
 * Each entity type in the registry declares a `shape`, and this module is the
 * one place that knows how to draw one. That split is deliberate: which glyph a
 * type wears is configuration an operator can change through the registry API,
 * while the finite set of glyphs the canvas can draw is code. Adding a type
 * needs no change here; inventing a genuinely new mark does.
 *
 * A shape the renderer does not implement falls back to the boxed dot rather
 * than vanishing, so a registry edit can never blank the canvas.
 *
 * Geometry follows `docs/mockups/observatory/index.html`. Stroke widths are
 * divided by the camera zoom so an outline stays the same physical weight
 * however far out the camera sits — a 2px rim drawn in world units becomes a
 * 0.6px hairline at 0.3× and the mark loses its identity.
 */

const TAU = Math.PI * 2;

/** The dark interior every lit glyph sits on, so rims read against the field. */
const CORE = '#05070d';

export interface GlyphOptions {
  /** Registry `shape` value. Unknown values draw the boxed dot. */
  shape: string;
  x: number;
  y: number;
  /** Radius, or half-side for the boxed shapes. */
  size: number;
  /** Base colour as an `r,g,b` triple. */
  colour: readonly [number, number, number];
  /** 0–1 emphasis. Dimmed neighbours draw at a fraction of full strength. */
  alpha: number;
  /** Animation clock in ms. */
  now: number;
  /** Camera zoom, so strokes hold a constant screen weight. */
  zoom: number;
  /** Suppress the rotating and pulsing parts. */
  reducedMotion?: boolean;
  /** Lifecycle state, where the glyph shows one — `waiting` lights a pip. */
  state?: string;
  /**
   * 0–1 completion arc, drawn outside the glyph. Models use it for
   * utilisation; anything with a real ratio can.
   */
  progress?: number;
}

function rgba(colour: readonly [number, number, number], alpha: number): string {
  const [r, g, b] = colour;
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
}

function hexRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
}

/** Stroke width in world units that renders as `px` screen pixels. */
function stroke(px: number, zoom: number): number {
  if (!Number.isFinite(zoom) || zoom <= 0) return px;
  return px / zoom;
}

export function roundRectPath(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
): void {
  const radius = Math.max(0, Math.min(r, Math.min(w, h) / 2));
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

export function polygonPath(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  r: number,
  sides: number,
  rotation: number,
): void {
  ctx.beginPath();
  for (let i = 0; i < sides; i += 1) {
    const t = rotation + (i / sides) * TAU;
    const x = cx + Math.cos(t) * r;
    const y = cy + Math.sin(t) * r;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

// ── Individual glyphs ─────────────────────────────────────────────────────────

/**
 * A resident: a lit core inside a slowly turning dashed orbit.
 *
 * The orbit is what separates an agent from a service at a glance — it is the
 * only mark on the canvas that moves of its own accord, which is the point,
 * since a resident is the only thing on the canvas that acts of its own accord.
 */
function drawAgent(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, now, zoom } = o;
  const stateColour =
    o.state === 'waiting' || o.state === 'degraded'
      ? ([245, 158, 11] as const)
      : o.state === 'learning'
        ? ([143, 212, 0] as const)
        : colour;

  ctx.save();
  ctx.translate(x, y);
  if (!o.reducedMotion) ctx.rotate(now / 7000);
  ctx.strokeStyle = rgba(stateColour, 0.5 * a);
  ctx.lineWidth = stroke(1.8, zoom);
  ctx.setLineDash([stroke(7, zoom), stroke(7, zoom)]);
  ctx.beginPath();
  ctx.arc(0, 0, size + 9, 0, TAU);
  ctx.stroke();
  ctx.restore();

  const glow = ctx.createRadialGradient(x, y, 0, x, y, size * 2.4);
  glow.addColorStop(0, rgba(colour, 0.4 * a));
  glow.addColorStop(1, rgba(colour, 0));
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(x, y, size * 2.4, 0, TAU);
  ctx.fill();

  ctx.fillStyle = hexRgba(CORE, a);
  ctx.beginPath();
  ctx.arc(x, y, size, 0, TAU);
  ctx.fill();
  ctx.strokeStyle = rgba(colour, 0.95 * a);
  ctx.lineWidth = stroke(2.4, zoom);
  ctx.stroke();

  ctx.fillStyle = rgba(colour, 0.9 * a);
  ctx.beginPath();
  ctx.arc(x, y, size * 0.36, 0, TAU);
  ctx.fill();

  if (o.state === 'waiting') {
    const pulse = o.reducedMotion ? 0.8 : 0.55 + 0.45 * Math.sin(now / 420);
    ctx.fillStyle = rgba([245, 158, 11], pulse * a);
    ctx.beginPath();
    ctx.arc(x + size * 0.78, y - size * 0.78, 4.4, 0, TAU);
    ctx.fill();
  }
}

/** A live workflow session: three offset arcs turning around a small core. */
function drawHalo(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, now, zoom } = o;

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(o.reducedMotion ? 0 : now / 5200);
  for (let i = 0; i < 3; i += 1) {
    ctx.strokeStyle = rgba(colour, (0.55 - i * 0.14) * a);
    ctx.lineWidth = stroke(2, zoom);
    ctx.beginPath();
    ctx.arc(0, 0, size + i * 7, i * 1.1, i * 1.1 + 2.1);
    ctx.stroke();
  }
  ctx.restore();

  ctx.fillStyle = hexRgba(CORE, a);
  ctx.beginPath();
  ctx.arc(x, y, size * 0.62, 0, TAU);
  ctx.fill();
  ctx.strokeStyle = rgba(colour, 0.9 * a);
  ctx.lineWidth = stroke(2, zoom);
  ctx.stroke();

  ctx.fillStyle = rgba(colour, 0.85 * a);
  ctx.beginPath();
  ctx.arc(x, y, size * 0.24, 0, TAU);
  ctx.fill();
}

/** A short-lived run agent. */
function drawTriangle(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  polygonPath(ctx, x, y, size, 3, -Math.PI / 2);
  ctx.fillStyle = rgba(colour, 0.2 * a);
  ctx.fill();
  ctx.strokeStyle = rgba(colour, 0.9 * a);
  ctx.lineWidth = stroke(1.6, zoom);
  ctx.stroke();
}

/** A machine: a rack face with shelves. */
function drawRack(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  ctx.fillStyle = hexRgba(CORE, a * 0.9);
  ctx.strokeStyle = rgba(colour, 0.85 * a);
  ctx.lineWidth = stroke(1.8, zoom);
  roundRectPath(ctx, x - size * 1.25, y - size * 0.78, size * 2.5, size * 1.56, 3.5);
  ctx.fill();
  ctx.stroke();

  ctx.strokeStyle = rgba(colour, 0.45 * a);
  ctx.lineWidth = stroke(1.2, zoom);
  for (let i = -1; i <= 1; i += 1) {
    ctx.beginPath();
    ctx.moveTo(x - size * 0.85, y + i * size * 0.42);
    ctx.lineTo(x + size * 0.85, y + i * size * 0.42);
    ctx.stroke();
  }
}

function drawPolygonGlyph(
  ctx: CanvasRenderingContext2D,
  o: GlyphOptions,
  sides: number,
  rotation: number,
  fillAlpha: number,
  strokeAlpha: number,
  width: number,
): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  polygonPath(ctx, x, y, size, sides, rotation);
  ctx.fillStyle = rgba(colour, fillAlpha * a);
  ctx.fill();
  ctx.strokeStyle = rgba(colour, strokeAlpha * a);
  ctx.lineWidth = stroke(width, zoom);
  ctx.stroke();
}

/** A model: a flat-topped hexagon, ringed by how hard it is being worked. */
function drawModel(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  drawPolygonGlyph(ctx, o, 6, Math.PI / 6, 0.14, 0.9, 1.8);
  const util = o.progress ?? 0;
  if (util <= 0) return;
  const { x, y, size, colour, alpha: a, zoom } = o;
  ctx.strokeStyle = rgba(colour, 0.9 * a);
  ctx.lineWidth = stroke(3, zoom);
  ctx.beginPath();
  ctx.arc(x, y, size + 7, -Math.PI / 2, -Math.PI / 2 + Math.min(1, util) * TAU);
  ctx.stroke();
}

/** A store: a cylinder seen slightly from above. */
function drawCylinder(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  ctx.strokeStyle = rgba(colour, 0.85 * a);
  ctx.fillStyle = rgba(colour, 0.14 * a);
  ctx.lineWidth = stroke(1.7, zoom);
  roundRectPath(ctx, x - size * 0.8, y - size * 0.9, size * 1.6, size * 1.8, size * 0.5);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.ellipse(x, y - size * 0.5, size * 0.8, size * 0.3, 0, 0, TAU);
  ctx.stroke();
}

/** A signal source: a filled dot that pings outward. */
function drawBeacon(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, now, zoom } = o;
  ctx.fillStyle = rgba(colour, 0.75 * a);
  ctx.beginPath();
  ctx.arc(x, y, size, 0, TAU);
  ctx.fill();
  if (o.reducedMotion) return;
  const phase = (now / 2200) % 1;
  ctx.strokeStyle = rgba(colour, 0.35 * (1 - phase) * a);
  ctx.lineWidth = stroke(1.6, zoom);
  ctx.beginPath();
  ctx.arc(x, y, size + phase * 26, 0, TAU);
  ctx.stroke();
}

/** Equipment: a hard-cornered box, no dot — it is a thing, not a process. */
function drawSquareSmall(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  ctx.fillStyle = rgba(colour, 0.16 * a);
  ctx.strokeStyle = rgba(colour, 0.8 * a);
  ctx.lineWidth = stroke(1.7, zoom);
  roundRectPath(ctx, x - size, y - size, size * 2, size * 2, 2);
  ctx.fill();
  ctx.stroke();
}

/** The default: a soft box with a lit centre. Every plain workload wears it. */
function drawBoxedDot(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  ctx.fillStyle = hexRgba(CORE, a * 0.9);
  ctx.strokeStyle = rgba(colour, 0.8 * a);
  ctx.lineWidth = stroke(1.7, zoom);
  roundRectPath(ctx, x - size, y - size, size * 2, size * 2, 4);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = rgba(colour, 0.75 * a);
  ctx.beginPath();
  ctx.arc(x, y, size * 0.3, 0, TAU);
  ctx.fill();
}

/** A boundary drawn as a glyph rather than a container — dashed rim, lit hub. */
function drawRing(ctx: CanvasRenderingContext2D, o: GlyphOptions): void {
  const { x, y, size, colour, alpha: a, zoom } = o;
  ctx.save();
  ctx.setLineDash([stroke(6, zoom), stroke(5, zoom)]);
  ctx.strokeStyle = rgba(colour, 0.8 * a);
  ctx.lineWidth = stroke(1.8, zoom);
  ctx.beginPath();
  ctx.arc(x, y, size, 0, TAU);
  ctx.stroke();
  ctx.restore();
  ctx.fillStyle = rgba(colour, 0.3 * a);
  ctx.beginPath();
  ctx.arc(x, y, size * 0.42, 0, TAU);
  ctx.fill();
}

// ── Dispatch ──────────────────────────────────────────────────────────────────

type GlyphFn = (ctx: CanvasRenderingContext2D, o: GlyphOptions) => void;

/**
 * Registry shape → glyph.
 *
 * Both the names the seed registry ships today and the finer marks the
 * mockup draws are listed, because an already-seeded deployment keeps whatever
 * it first stored: the registry merge is additive and never overwrites an
 * operator's type. `rounded-rect` and `rack` therefore have to mean the same
 * machine, and `diamond`/`chevron` the same resident, or a live cluster would
 * render fallbacks after this change.
 */
const GLYPHS: Readonly<Record<string, GlyphFn>> = {
  agent: drawAgent,
  diamond: drawAgent,
  chevron: drawAgent,
  halo: drawHalo,
  triangle: drawTriangle,
  rack: drawRack,
  'rounded-rect': drawRack,
  pentagon: (ctx, o) => drawPolygonGlyph(ctx, o, 5, -Math.PI / 2, 0.18, 0.95, 2),
  hex: (ctx, o) => drawPolygonGlyph(ctx, o, 6, 0, 0.16, 0.9, 1.8),
  'hex-flat': drawModel,
  cylinder: drawCylinder,
  beacon: drawBeacon,
  'square-sm': drawSquareSmall,
  square: drawBoxedDot,
  dot: drawBoxedDot,
  ring: drawRing,
  'ring-dashed': drawRing,
};

/** True when the canvas knows how to draw this registry shape. */
export function isKnownShape(shape: string): boolean {
  return shape in GLYPHS;
}

/**
 * Draw one entity glyph.
 *
 * `mimir` and `mimir-metrics` are deliberately absent: they carry their own
 * ambient animation and are drawn last, above everything else.
 */
export function drawGlyph(ctx: CanvasRenderingContext2D, options: GlyphOptions): void {
  const glyph = GLYPHS[options.shape] ?? drawBoxedDot;
  ctx.save();
  ctx.lineCap = 'round';
  glyph(ctx, options);
  ctx.setLineDash([]);
  ctx.restore();
}

/**
 * How far a glyph reaches from its centre.
 *
 * Used to trim edges to the outline and to place labels, so a triangle's
 * label does not sit inside it and a rack's edges do not run under its face.
 */
export function glyphRadius(shape: string, size: number): number {
  switch (shape) {
    case 'agent':
    case 'diamond':
    case 'chevron':
      return size + 9;
    case 'halo':
      return size + 14;
    case 'rack':
    case 'rounded-rect':
      return size * 1.25;
    case 'hex-flat':
      return size + 7;
    case 'square':
    case 'square-sm':
    case 'dot':
      return size * 1.42;
    default:
      return size;
  }
}
