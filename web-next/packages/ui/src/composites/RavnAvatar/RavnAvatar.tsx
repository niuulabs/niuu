import { cn } from '../../utils/cn';
import type { PersonaRole } from '@niuulabs/domain';
import { StateDot, type DotState } from '../../primitives/StateDot';
import { PersonaShapeRenderer, PERSONA_ROLE_SHAPE } from '../PersonaAvatar';
import './RavnAvatar.css';

export interface RavnAvatarProps {
  /** Persona role — drives the shape outline. */
  role: PersonaRole;
  /** Rune glyph rendered inside the shape (e.g. 'ᚱ'). */
  rune: string;
  /** Runtime state — drives the corner dot color and pulse. */
  state: DotState;
  size?: number;
  /** Pulse the state dot for active states. */
  pulse?: boolean;
  title?: string;
  className?: string;
}

/**
 * Ravn avatar: role-shape outline + rune + corner state dot.
 * Used by both the Ravn module (fleet list) and the Mímir module (ravn bindings).
 */
export function RavnAvatar({
  role,
  rune,
  state,
  size = 28,
  pulse = false,
  title,
  className,
}: RavnAvatarProps) {
  const shape = PERSONA_ROLE_SHAPE[role];
  const dotSize = Math.max(6, Math.round(size * 0.33));

  return (
    <span
      className={cn('niuu-ravn-av', className)}
      title={title ?? `${rune} · ${role} · ${state}`}
      aria-label={title ?? `ravn ${role} ${state}`}
      style={{ width: size, height: size }}
    >
      <PersonaShapeRenderer shape={shape} size={size} />
      <span
        className="niuu-ravn-av__rune"
        style={{ fontSize: Math.round(size * 0.46) }}
        aria-hidden
      >
        {rune}
      </span>
      <StateDot state={state} size={dotSize} pulse={pulse} className="niuu-ravn-av__dot" />
    </span>
  );
}
