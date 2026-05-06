interface LogoTreeProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

/**
 * Yggdrasil logo — a vertical world-tree axis with three realm planes.
 * Uses currentColor so it inherits the brand palette from the parent.
 */
export function LogoTree({ size = 56, stroke = 1.4, glow = false }: LogoTreeProps) {
  return (
    <svg
      viewBox="0 0 56 56"
      width={size}
      height={size}
      aria-hidden
      style={{ filter: glow ? 'drop-shadow(0 0 8px currentColor)' : undefined }}
    >
      <g fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round">
        {/* trunk */}
        <path d="M28 6 V50" />
        {/* three realm planes — Asgard, Midgard, Svartalfheim */}
        <path d="M14 14 H42" opacity={0.5} />
        <path d="M10 28 H46" />
        <path d="M16 42 H40" opacity={0.6} />
        {/* branch diagonals */}
        <path d="M28 14 L16 24 M28 14 L40 24" opacity={0.6} />
        <path d="M28 28 L12 38 M28 28 L44 38" opacity={0.4} />
        {/* nodes */}
        <circle cx={28} cy={6} r={1.8} fill="currentColor" />
        <circle cx={28} cy={28} r={2.4} fill="currentColor" />
        <circle cx={28} cy={50} r={1.8} fill="currentColor" />
        <circle cx={14} cy={14} r={1.2} fill="currentColor" />
        <circle cx={42} cy={14} r={1.2} fill="currentColor" />
        <circle cx={10} cy={28} r={1.2} fill="currentColor" />
        <circle cx={46} cy={28} r={1.2} fill="currentColor" />
      </g>
    </svg>
  );
}
