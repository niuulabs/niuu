interface LogoStarsProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

// Dot positions: [cx, cy] pairs forming the "niuu" connect-the-dots word
const DOTS: [number, number][] = [
  [6, 38],
  [6, 22],
  [14, 22],
  [14, 38],
  [22, 38],
  [22, 22],
  [22, 16],
  [30, 22],
  [34, 38],
  [38, 22],
  [42, 22],
  [46, 38],
  [50, 22],
];

/**
 * Stars logo — "niuu" rendered as a connect-the-dots constellation.
 * Uses currentColor so it inherits the brand palette from the parent.
 */
export function LogoStars({ size = 56, stroke = 1.2, glow = false }: LogoStarsProps) {
  return (
    <svg
      viewBox="0 0 56 56"
      width={size}
      height={size}
      aria-hidden
      style={{ filter: glow ? 'drop-shadow(0 0 6px currentColor)' : undefined }}
    >
      <g fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" opacity={0.7}>
        {/* n */}
        <path d="M6 38 V22 L14 38 V22" />
        {/* i dot + stem */}
        <path d="M22 22 V38" />
        {/* u */}
        <path d="M30 22 V34 Q30 38 34 38 Q38 38 38 34 V22" />
        {/* u */}
        <path d="M42 22 V34 Q42 38 46 38 Q50 38 50 34 V22" />
      </g>
      <g fill="currentColor">
        {DOTS.map(([cx, cy], i) => (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={i % 3 === 0 ? 1.6 : 1}
            opacity={i % 3 === 0 ? 1 : 0.6}
          />
        ))}
      </g>
    </svg>
  );
}
