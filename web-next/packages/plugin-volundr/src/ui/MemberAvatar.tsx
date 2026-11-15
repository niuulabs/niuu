import { cn, resolveParticipantColor } from '@niuulabs/ui';
import './NewTeam.css';

/**
 * The colour of the room's n-th member (agents first, then people), the palette slot the room
 * assigns in join order, so a member reads the same on the new team page and in the list.
 */
export function memberColor(index: number): string {
  return resolveParticipantColor('', `p${index + 1}`);
}

/**
 * One member of a room: an agent (its engine's rune) or a person (their initials), in a ring
 * tinted with `color`. Used in the room preview of the new team page and in the session list.
 */
export function MemberAvatar({
  kind,
  glyph,
  color,
  small = false,
  title,
}: {
  kind: 'agent' | 'person';
  glyph: string;
  color: string;
  small?: boolean;
  title?: string;
}) {
  return (
    <span
      aria-hidden="true"
      title={title}
      data-kind={kind}
      className={cn(
        'vol-agent-avatar',
        kind === 'person' && 'vol-agent-avatar--person',
        small && 'vol-agent-avatar--sm',
      )}
      style={{ '--agent-color': color } as React.CSSProperties}
    >
      {glyph}
    </span>
  );
}
