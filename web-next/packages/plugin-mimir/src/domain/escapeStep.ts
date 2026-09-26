/**
 * Escape-key precedence for the Memory Explore view: clears a traced path
 * first, then focus, then exits replay — one step per press.
 */

export interface MemoryEscapableState {
  /** Ordered node ids of a shift-click-traced path. Empty when none traced. */
  path: string[];
  focus: string | null;
  asOf: string | null;
}

export type MemoryEscapeField = 'path' | 'focus' | 'asOf';

/** Which field Escape should clear next, or null when there is nothing left to step back out of. */
export function escapeStep(state: MemoryEscapableState): MemoryEscapeField | null {
  if (state.path.length > 0) return 'path';
  if (state.focus !== null) return 'focus';
  if (state.asOf !== null) return 'asOf';
  return null;
}
