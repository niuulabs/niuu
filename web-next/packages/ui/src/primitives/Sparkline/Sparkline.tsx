import { cn } from '../../utils/cn';

const DEFAULT_WIDTH = 120;
const DEFAULT_HEIGHT = 28;
const DEFAULT_SAMPLE_COUNT = 24;
const PAD = 2;
const STROKE_WIDTH = 1.2;

/** Mulberry32 seeded PRNG — deterministic from a numeric seed. */
function seededRng(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** FNV-1a-style hash of a string → positive integer. */
function hashId(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Generate a smooth random walk clamped to [0, 1]. */
function generateValues(id: string, count: number): number[] {
  const rng = seededRng(hashId(id));
  const values: number[] = [rng()];
  for (let i = 1; i < count; i++) {
    const prev = values[i - 1]!;
    values.push(Math.max(0, Math.min(1, prev + (rng() - 0.45) * 0.3)));
  }
  return values;
}

export interface SparklineProps {
  /** Data points to plot. Falls back to deterministic values derived from `id`. */
  values?: number[];
  /** Seed for deterministic fallback data generation. */
  id?: string;
  width?: number;
  height?: number;
  /** Whether to fill the area under the line. */
  fill?: boolean;
  className?: string;
}

export function Sparkline({
  values: valuesProp,
  id = 'default',
  width = DEFAULT_WIDTH,
  height = DEFAULT_HEIGHT,
  fill = true,
  className,
}: SparklineProps) {
  const values = valuesProp ?? generateValues(id, DEFAULT_SAMPLE_COUNT);

  if (values.length === 0) {
    return (
      <svg
        width={width}
        height={height}
        className={cn('niuu-sparkline', className)}
        style={{ display: 'block' }}
        aria-hidden="true"
      />
    );
  }

  if (values.length === 1) {
    const cx = width / 2;
    const cy = height / 2;
    return (
      <svg
        width={width}
        height={height}
        className={cn('niuu-sparkline', className)}
        style={{ display: 'block' }}
        aria-hidden="true"
      >
        <circle cx={cx} cy={cy} r={2} fill="var(--brand-300)" />
      </svg>
    );
  }

  const max = Math.max(...values, 0.001);
  const min = 0;
  const range = max - min || 1;

  const pts: [number, number][] = values.map((v, i) => [
    PAD + (i / (values.length - 1)) * (width - 2 * PAD),
    PAD + (1 - (v - min) / range) * (height - 2 * PAD),
  ]);

  const linePath = pts
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');

  const areaPath =
    linePath +
    ` L${pts[pts.length - 1]![0].toFixed(1)},${(height - PAD).toFixed(1)}` +
    ` L${pts[0]![0].toFixed(1)},${(height - PAD).toFixed(1)} Z`;

  return (
    <svg
      width={width}
      height={height}
      className={cn('niuu-sparkline', className)}
      style={{ display: 'block' }}
      aria-hidden="true"
    >
      {fill && <path d={areaPath} fill="color-mix(in srgb, var(--brand-300) 14%, transparent)" />}
      <path d={linePath} fill="none" stroke="var(--brand-300)" strokeWidth={STROKE_WIDTH} />
    </svg>
  );
}
