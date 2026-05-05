interface LogoStackProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

/**
 * Stack logo — three stacked ᚾ (Naudhiz) runes, the N of niuu.
 * Uses currentColor so it inherits the brand palette from the parent.
 */
export function LogoStack({ size = 56, stroke = 1.6, glow = false }: LogoStackProps) {
  return (
    <svg
      viewBox="0 0 56 56"
      width={size}
      height={size}
      aria-hidden
      style={{ filter: glow ? 'drop-shadow(0 0 8px currentColor)' : undefined }}
    >
      <g fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round">
        {/* vertical staves */}
        <path d="M14 10 V46" />
        <path d="M28 10 V46" />
        <path d="M42 10 V46" />
        {/* naudhiz cross-strokes */}
        <path d="M14 22 L22 14" opacity={0.9} />
        <path d="M28 30 L36 22" />
        <path d="M42 38 L50 30" opacity={0.9} />
      </g>
    </svg>
  );
}
