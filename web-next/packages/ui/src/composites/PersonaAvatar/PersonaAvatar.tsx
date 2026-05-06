import { cn } from '../../utils/cn';
import type { PersonaRole } from '@niuulabs/domain';
import './PersonaAvatar.css';

export type PersonaShapeKind =
  | 'ring'
  | 'ring-dashed'
  | 'square'
  | 'chevron'
  | 'hex'
  | 'triangle'
  | 'diamond'
  | 'dot-in-ring'
  | 'rounded';

/**
 * Deterministic role → shape mapping. Every call with the same role returns
 * the same shape — this is contractually guaranteed and tested.
 *
 * Exported so Tyr, Observatory, and other consumers can derive the shape
 * without rendering the full avatar.
 */
export const PERSONA_ROLE_SHAPE: Record<PersonaRole, PersonaShapeKind> = {
  // Active roles (web2 baseline)
  arbiter: 'diamond',
  autonomy: 'triangle',
  build: 'square',
  coord: 'chevron',
  investigate: 'ring-dashed',
  knowledge: 'dot-in-ring',
  observe: 'hex',
  plan: 'chevron',
  qa: 'ring-dashed',
  report: 'rounded',
  review: 'ring',
  write: 'rounded',
  // Legacy roles — kept for backward compatibility
  audit: 'hex',
  gate: 'diamond',
  index: 'dot-in-ring',
  ship: 'triangle',
  verify: 'ring-dashed',
};

const FILL = 'color-mix(in srgb, var(--brand-500) 14%, transparent)';
const STROKE = 'var(--brand-300)';
const SW = 1.4;

export interface PersonaShapeRendererProps {
  shape: PersonaShapeKind;
  size: number;
}

/**
 * Renders the SVG shape outline for a persona role.
 * Exported so RavnAvatar can reuse the exact same geometry.
 */
export function PersonaShapeRenderer({ shape, size }: PersonaShapeRendererProps) {
  const S = size;
  const H = S / 2;
  const r = H - SW;
  const common = {
    width: S,
    height: S,
    viewBox: `0 0 ${S} ${S}`,
    'aria-hidden': true as const,
    style: { display: 'block' as const },
  };

  switch (shape) {
    case 'ring':
      return (
        <svg {...common}>
          <circle cx={H} cy={H} r={r} fill={FILL} stroke={STROKE} strokeWidth={SW} />
        </svg>
      );
    case 'ring-dashed':
      return (
        <svg {...common}>
          <circle
            cx={H}
            cy={H}
            r={r}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
            strokeDasharray="2 2"
          />
        </svg>
      );
    case 'square':
      return (
        <svg {...common}>
          <rect
            x={SW}
            y={SW}
            width={S - 2 * SW}
            height={S - 2 * SW}
            rx={2}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
          />
        </svg>
      );
    case 'rounded':
      return (
        <svg {...common}>
          <rect
            x={SW}
            y={SW + S * 0.15}
            width={S - 2 * SW}
            height={S - 2 * SW - S * 0.3}
            rx={S * 0.18}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
          />
        </svg>
      );
    case 'diamond':
      return (
        <svg {...common}>
          <path
            d={`M${H} ${SW} L${S - SW} ${H} L${H} ${S - SW} L${SW} ${H} Z`}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
          />
        </svg>
      );
    case 'triangle':
      return (
        <svg {...common}>
          <path
            d={`M${H} ${SW} L${S - SW} ${S - SW} L${SW} ${S - SW} Z`}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
          />
        </svg>
      );
    case 'chevron':
      return (
        <svg {...common}>
          <path
            d={`M${SW} ${S - SW} L${H} ${SW + S * 0.1} L${S - SW} ${S - SW} L${H} ${S * 0.66} Z`}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
            strokeLinejoin="round"
          />
        </svg>
      );
    case 'hex': {
      const dx = S * 0.18;
      return (
        <svg {...common}>
          <path
            d={`M${dx} ${H} L${H} ${SW} L${S - dx} ${H} L${S - dx} ${S - SW} L${H} ${S - SW + 0.001} L${dx} ${S - SW} Z`}
            fill={FILL}
            stroke={STROKE}
            strokeWidth={SW}
            strokeLinejoin="round"
          />
        </svg>
      );
    }
    case 'dot-in-ring':
      return (
        <svg {...common}>
          <circle cx={H} cy={H} r={r} fill={FILL} stroke={STROKE} strokeWidth={SW} />
          <circle cx={H} cy={H} r={S * 0.18} fill={STROKE} />
        </svg>
      );
  }
}

export interface PersonaAvatarProps {
  /** Functional role — drives the shape. */
  role: PersonaRole;
  /** Single uppercase letter rendered inside the shape. */
  letter: string;
  size?: number;
  title?: string;
  className?: string;
}

/**
 * Persona avatar: role-derived SVG shape outline with a single letter inside.
 * Same role always maps to the same shape (deterministic, tested).
 */
export function PersonaAvatar({ role, letter, size = 22, title, className }: PersonaAvatarProps) {
  const shape = PERSONA_ROLE_SHAPE[role];
  const displayLetter = letter.charAt(0).toUpperCase();

  return (
    <span
      className={cn('niuu-persona-av', className)}
      title={title ?? `${role} · ${displayLetter}`}
      aria-label={title ?? `${role} persona`}
      style={{ width: size, height: size }}
    >
      <PersonaShapeRenderer shape={shape} size={size} />
      <span
        className="niuu-persona-av__letter"
        style={{ fontSize: Math.round(size * 0.46) }}
        aria-hidden
      >
        {displayLetter}
      </span>
    </span>
  );
}
