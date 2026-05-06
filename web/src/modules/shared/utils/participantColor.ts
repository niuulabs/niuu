/**
 * Number of brand-derived participant color slots available.
 * Each slot is defined in tokens.css as --color-participant-{n}.
 */
export const PARTICIPANT_SLOT_COUNT = 7;

/**
 * Map participant slot names (p1–p7) to their CSS custom properties.
 * These properties derive from --color-brand via oklch color-mix,
 * so they stay cohesive with the selected theme.
 */
export const PARTICIPANT_COLOR_MAP: Record<string, string> = Object.fromEntries(
  Array.from({ length: PARTICIPANT_SLOT_COUNT }, (_, i) => [
    `p${i + 1}`,
    `var(--color-participant-${i + 1})`,
  ])
);

/**
 * Resolve a participant color slot name to a CSS variable reference.
 * Falls back to --color-text-secondary for unknown slots.
 */
export function resolveParticipantColor(color: string): string {
  return PARTICIPANT_COLOR_MAP[color] ?? 'var(--color-text-secondary)';
}

/**
 * Return the slot name for a given participant index (0-based).
 * Wraps around when more participants than slots.
 */
export function participantSlot(index: number): string {
  return `p${(index % PARTICIPANT_SLOT_COUNT) + 1}`;
}
