import type { PersonaRole } from '@niuulabs/domain';
import type { DreamCycle } from './lint';

export type RavnState = 'active' | 'idle' | 'offline';

/**
 * Mímir ravn binding — maps a deployed ravn to its mount access and last
 * dream-cycle run.
 *
 * Bindings are used in the Ravns view to display per-ravn mount assignments
 * and surfaced dream-cycle metrics (pages_updated / entities_created / lint_fixes).
 */
export interface RavnBinding {
  /** Unique ravn identifier (matches the persona id). */
  ravnId: string;
  /** Rune glyph for this ravn's persona (e.g. 'ᚱ'). */
  ravnRune: string;
  role: PersonaRole;
  /** Runtime state of the ravn. */
  state: RavnState;
  /** Names of the mounts this ravn is allowed to read from. */
  mountNames: string[];
  /** Name of the mount this ravn writes to by default. */
  writeMount: string;
  /** Most recent dream cycle run by this ravn, or null if none yet. */
  lastDream: DreamCycle | null;
  /** Short bio describing this ravn's purpose, shown in overview warden cards. */
  bio: string;
  /** Total pages this ravn has touched across all dream cycles. */
  pagesTouched: number;
  /** Areas of domain expertise (e.g. 'kubernetes', 'networking'). */
  expertise: string[];
  /** Tool names this ravn is allowed to invoke (e.g. 'mimir', 'web', 'file'). */
  tools: string[];
}
