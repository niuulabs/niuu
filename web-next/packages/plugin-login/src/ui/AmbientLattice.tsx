import { useReducedMotion } from './useReducedMotion';
import './ambient.css';

const RUNES = ['ᚲ', 'ᛃ', 'ᚱ', 'ᛗ', 'ᚨ', 'ᛖ', 'ᚠ', 'ᛒ', 'ᛞ', 'ᛜ', 'ᚾ', 'ᚲ'] as const;
const RUNE_ORBIT_R = 340;
const RUNE_ROTATION_DUR = '220s';

/**
 * Lattice ambient — concentric faint circles with a slowly rotating rune band.
 *
 * The `<animateTransform>` is omitted when `prefers-reduced-motion` is set,
 * leaving the rune ring static.
 */
export function AmbientLattice() {
  const reduced = useReducedMotion();

  const spokeTicks = Array.from({ length: 12 }, (_, i) => {
    const angle = (i / 12) * Math.PI * 2;
    return {
      x1: 200 * Math.cos(angle),
      y1: 200 * Math.sin(angle),
      x2: 280 * Math.cos(angle),
      y2: 280 * Math.sin(angle),
    };
  });

  const runePositions = RUNES.map((rune, i) => {
    const angle = (i / RUNES.length) * Math.PI * 2;
    return {
      rune,
      x: RUNE_ORBIT_R * Math.cos(angle),
      y: RUNE_ORBIT_R * Math.sin(angle) + 4,
    };
  });

  return (
    <svg
      className="login-ambient"
      viewBox="0 0 800 600"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
      data-testid="ambient-lattice"
    >
      <defs>
        <radialGradient id="lat-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" className="ambient-stop-brand" stopOpacity="0.08" />
          <stop offset="60%" className="ambient-stop-brand" stopOpacity="0.02" />
          <stop offset="100%" className="ambient-stop-bg" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="800" height="600" fill="url(#lat-glow)" />

      <g
        transform="translate(400 300)"
        fill="none"
        className="lattice-rings-group"
        data-testid="lattice-main"
      >
        {/* Concentric rings */}
        <circle r={120} strokeWidth="0.6" data-ring />
        <circle r={200} strokeWidth="0.6" data-ring />
        <circle r={280} strokeWidth="0.5" strokeDasharray="2 4" data-ring />
        <circle r={380} strokeWidth="0.4" strokeDasharray="4 8" data-ring />

        {/* Spoke ticks between ring 200 and 280 */}
        {spokeTicks.map((t, i) => (
          <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} strokeWidth="0.6" data-spoke />
        ))}

        {/* Rotating rune band */}
        <g data-testid="lattice-rune-band">
          {!reduced && (
            <animateTransform
              attributeName="transform"
              type="rotate"
              from="0"
              to="360"
              dur={RUNE_ROTATION_DUR}
              repeatCount="indefinite"
            />
          )}
          {runePositions.map(({ rune, x, y }, i) => (
            <text
              key={i}
              x={x}
              y={y}
              textAnchor="middle"
              fontFamily="JetBrainsMono NF, monospace"
              fontSize="12"
              className="lattice-rune-text"
            >
              {rune}
            </text>
          ))}
        </g>
      </g>
    </svg>
  );
}
