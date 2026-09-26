/**
 * Reads the memory-scene colour tokens from the DOM at runtime.
 *
 * Colours must come from design tokens, never a hard-coded hex in a
 * component — this is the one place that reads `getComputedStyle`, so the
 * scene's WebGL materials and the legend's Tailwind swatches always agree:
 * both call `memoryPalette(el)` and get the same values back.
 */

import type { KindGroup } from '../../domain/memoryKinds';
import type { AgeBucketId } from '../../domain/ageBucket';

export type ProofBucket = 'high' | 'medium' | 'low' | 'none';
/** Re-exported under the palette's own naming; identical to `AgeBucketId`. */
export type AgeBucket = AgeBucketId;

export interface MemoryPalette {
  kind: Record<KindGroup, string>;
  proof: Record<ProofBucket, string>;
  age: Record<AgeBucket, string>;
  dispute: string;
}

const KIND_VARS: Record<KindGroup, string> = {
  topic: '--color-memory-kind-topic',
  entity: '--color-memory-kind-entity',
  decision: '--color-memory-kind-decision',
  directive: '--color-memory-kind-directive',
  preference: '--color-memory-kind-preference',
  goal: '--color-memory-kind-goal',
  observation: '--color-memory-kind-observation',
  thread: '--color-memory-kind-thread',
  page: '--color-memory-kind-page',
};

const PROOF_VARS: Record<ProofBucket, string> = {
  high: '--color-memory-proof-high',
  medium: '--color-memory-proof-medium',
  low: '--color-memory-proof-low',
  none: '--color-memory-proof-none',
};

const AGE_VARS: Record<AgeBucket, string> = {
  today: '--color-memory-age-today',
  'this-week': '--color-memory-age-this-week',
  'this-month': '--color-memory-age-this-month',
  older: '--color-memory-age-older',
};

const DISPUTE_VAR = '--color-memory-dispute';

/** Every custom property the palette reads, for installing them in tests and for the error below. */
export const MEMORY_PALETTE_VARS: readonly string[] = [
  ...Object.values(KIND_VARS),
  ...Object.values(PROOF_VARS),
  ...Object.values(AGE_VARS),
  DISPUTE_VAR,
];

function mapRecord<K extends string>(
  vars: Record<K, string>,
  read: (name: string) => string,
): Record<K, string> {
  const entries = (Object.keys(vars) as K[]).map((key) => [key, read(vars[key])] as const);
  return Object.fromEntries(entries) as Record<K, string>;
}

/**
 * Read the memory palette from the computed styles of `el` (or its ancestors).
 *
 * Throws when any token is unresolved: a scene drawn in guessed colours would
 * disagree with its own legend, so a missing stylesheet must be loud.
 */
export function memoryPalette(el: HTMLElement): MemoryPalette {
  const styles = getComputedStyle(el);
  const missing: string[] = [];
  const read = (name: string): string => {
    const value = styles.getPropertyValue(name).trim();
    if (value.length === 0) missing.push(name);
    return value;
  };
  const palette: MemoryPalette = {
    kind: mapRecord(KIND_VARS, read),
    proof: mapRecord(PROOF_VARS, read),
    age: mapRecord(AGE_VARS, read),
    dispute: read(DISPUTE_VAR),
  };
  if (missing.length > 0) {
    throw new Error(
      `Memory colour tokens are not defined: ${missing.join(', ')}. ` +
        'Import @niuulabs/design-tokens/tokens.css on the host page.',
    );
  }
  return palette;
}
