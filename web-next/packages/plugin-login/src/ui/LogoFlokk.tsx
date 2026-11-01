interface LogoFlokkProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

const HEX_R = 14;

function hexPoints(cx: number, cy: number): string {
  return Array.from({ length: 6 }, (_, i) => {
    const angle = (i / 6) * Math.PI * 2 - Math.PI / 2;
    return `${cx + HEX_R * Math.cos(angle)},${cy + HEX_R * Math.sin(angle)}`;
  }).join(' ');
}

/**
 * Flokk logo — two interlocking hexagons, a flight / group metaphor.
 * Uses currentColor so it inherits the brand palette from the parent.
 */
export function LogoFlokk({ size = 56, stroke = 1.5, glow = false }: LogoFlokkProps) {
  return (
    <svg
      viewBox="0 0 56 56"
      width={size}
      height={size}
      aria-hidden
      style={{ filter: glow ? 'drop-shadow(0 0 8px currentColor)' : undefined }}
    >
      <g fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinejoin="round">
        <polygon points={hexPoints(22, 28)} opacity={0.9} />
        <polygon points={hexPoints(34, 28)} opacity={0.7} />
        <circle cx={28} cy={28} r={2} fill="currentColor" />
      </g>
    </svg>
  );
}
