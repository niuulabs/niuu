interface LogoKnotProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

/**
 * The Niuu knot logo — two interlocked N's as a Norse-knot braid.
 * Uses currentColor so it inherits the brand palette from the parent.
 */
export function LogoKnot({ size = 56, stroke = 1.6, glow = false }: LogoKnotProps) {
  return (
    <svg
      viewBox="0 0 56 56"
      width={size}
      height={size}
      aria-hidden
      style={{ filter: glow ? 'drop-shadow(0 0 8px currentColor)' : undefined }}
    >
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* left N */}
        <path d="M10 44 V12 L28 38 V14" />
        {/* right N, interlocking */}
        <path d="M46 44 V16 L28 42 V14" opacity={0.85} />
        {/* braid crossing */}
        <circle cx={28} cy={28} r={1.6} fill="currentColor" />
      </g>
    </svg>
  );
}
