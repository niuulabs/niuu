import { useMemo } from 'react';
import { useReducedMotion } from './useReducedMotion';
import './ambient.css';

const STAR_COUNT = 80;

interface Star {
  x: number;
  y: number;
  r: number;
  dur: number;
  delay: number;
}

function buildStars(): Star[] {
  return Array.from({ length: STAR_COUNT }, () => ({
    x: Math.random() * 100,
    y: Math.random() * 100,
    r: 0.4 + Math.random() * 1.2,
    dur: 2 + Math.random() * 4,
    delay: Math.random() * 4,
  }));
}

/**
 * Constellation ambient — 80 static stars with a slow shimmer.
 *
 * SVG `<animate>` elements are omitted when `prefers-reduced-motion` is set.
 */
export function AmbientConstellation() {
  const reduced = useReducedMotion();
  // Stable star positions — same on every render
  const stars = useMemo(buildStars, []);

  return (
    <svg
      className="login-ambient"
      viewBox="0 0 100 100"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
      data-testid="ambient-constellation"
    >
      <defs>
        <radialGradient id="const-glow" cx="50%" cy="40%" r="60%">
          <stop offset="0%" className="ambient-stop-mid" stopOpacity="0.3" />
          <stop offset="100%" className="ambient-stop-bg" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="100" height="100" fill="url(#const-glow)" />
      {stars.map((s, i) => (
        <circle
          key={i}
          cx={s.x}
          cy={s.y}
          r={s.r}
          className="constellation-star"
          opacity={reduced ? 0.5 : undefined}
        >
          {!reduced && (
            <animate
              attributeName="opacity"
              values="0.2;0.9;0.2"
              dur={`${s.dur}s`}
              begin={`${s.delay}s`}
              repeatCount="indefinite"
            />
          )}
        </circle>
      ))}
    </svg>
  );
}
