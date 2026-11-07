const PARTICIPANT_COLORS = [
  '#38bdf8', // sky-400
  '#a78bfa', // violet-400
  '#818cf8', // indigo-400
  '#f472b6', // pink-400
  '#fb923c', // orange-400
  '#facc15', // yellow-400
  '#22d3ee', // cyan-400
  '#f87171', // red-400
] as const;

/** Skuld names a palette slot ("p1"…"p7", `room.participant_colors`), not a CSS colour. */
const PALETTE_SLOT = /^p([1-9]\d*)$/;

/**
 * Returns a deterministic color for a participant based on their peerId.
 * A palette slot the room assigned maps onto the palette; any other explicit colour is CSS.
 * Falls back to the first color if peerId is empty.
 */
export function resolveParticipantColor(peerId: string, explicitColor?: string): string {
  const slot = explicitColor ? PALETTE_SLOT.exec(explicitColor) : null;
  if (slot) {
    const index = (Number(slot[1]) - 1) % PARTICIPANT_COLORS.length;
    return PARTICIPANT_COLORS[index] ?? PARTICIPANT_COLORS[0];
  }
  if (explicitColor) return explicitColor;
  if (!peerId) return PARTICIPANT_COLORS[0];
  let hash = 0;
  for (let i = 0; i < peerId.length; i++) {
    hash = (hash * 31 + peerId.charCodeAt(i)) >>> 0;
  }
  return PARTICIPANT_COLORS[hash % PARTICIPANT_COLORS.length] ?? PARTICIPANT_COLORS[0];
}
