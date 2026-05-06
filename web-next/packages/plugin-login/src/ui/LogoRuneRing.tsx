interface LogoRuneRingProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

const RUNES = ['ᚲ', 'ᛃ', 'ᚱ', 'ᛗ', 'ᚨ', 'ᛖ', 'ᚠ', 'ᛒ'] as const;
const ORBIT_R = 22;

/**
 * RuneRing logo — a ring of 8 entity runes around a central ᚾ (Naudhiz).
 * Uses currentColor so it inherits the brand palette from the parent.
 */
export function LogoRuneRing({ size = 56, stroke = 1.3, glow = false }: LogoRuneRingProps) {
  return (
    <svg
      viewBox="0 0 56 56"
      width={size}
      height={size}
      aria-hidden
      style={{ filter: glow ? 'drop-shadow(0 0 8px currentColor)' : undefined }}
    >
      <circle
        cx={28}
        cy={28}
        r={ORBIT_R}
        fill="none"
        stroke="currentColor"
        strokeWidth="0.7"
        opacity={0.35}
      />
      <circle
        cx={28}
        cy={28}
        r={12}
        fill="none"
        stroke="currentColor"
        strokeWidth={stroke}
        opacity={0.7}
      />

      {RUNES.map((rune, i) => {
        const angle = (i / RUNES.length) * Math.PI * 2 - Math.PI / 2;
        const x = 28 + ORBIT_R * Math.cos(angle);
        const y = 28 + ORBIT_R * Math.sin(angle) + 2;
        return (
          <text
            key={i}
            x={x}
            y={y}
            textAnchor="middle"
            fontFamily="JetBrainsMono NF, monospace"
            fontSize="7"
            fill="currentColor"
            opacity={0.55}
          >
            {rune}
          </text>
        );
      })}

      <text
        x={28}
        y={33}
        textAnchor="middle"
        fontFamily="JetBrainsMono NF, monospace"
        fontSize="16"
        fill="currentColor"
        fontWeight="600"
      >
        ᚾ
      </text>
    </svg>
  );
}
