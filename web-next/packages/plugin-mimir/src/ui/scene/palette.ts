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

/**
 * Fallback hex values, matching `design-tokens/src/tokens.css` exactly.
 * Used only when a variable can't be resolved (no stylesheet loaded — e.g.
 * a bare jsdom test environment, or a consumer who forgot to import
 * `@niuulabs/design-tokens/tokens.css`) so the scene still renders in
 * recognisably distinct colours rather than invisible/transparent nodes.
 */
const FALLBACK: MemoryPalette = {
  kind: {
    topic: '#38bdf8',
    entity: '#a855f7',
    decision: '#f59e0b',
    directive: '#ef4444',
    preference: '#22c55e',
    goal: '#f97316',
    observation: '#06b6d4',
    thread: '#6366f1',
    page: '#71717a',
  },
  proof: {
    high: '#22c55e',
    medium: '#f59e0b',
    low: '#f97316',
    none: '#71717a',
  },
  age: {
    today: '#38bdf8',
    'this-week': '#0ea5e9',
    'this-month': '#6366f1',
    older: '#52525b',
  },
  dispute: '#f59e0b',
};

function readVar(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = styles.getPropertyValue(name)?.trim();
  return value && value.length > 0 ? value : fallback;
}

function mapRecord<K extends string>(
  vars: Record<K, string>,
  fallback: Record<K, string>,
  styles: CSSStyleDeclaration,
): Record<K, string> {
  const entries = (Object.keys(vars) as K[]).map(
    (key) => [key, readVar(styles, vars[key], fallback[key])] as const,
  );
  return Object.fromEntries(entries) as Record<K, string>;
}

/** Read the memory palette from the computed styles of `el` (or its ancestors). */
export function memoryPalette(el: HTMLElement): MemoryPalette {
  const styles = getComputedStyle(el);
  return {
    kind: mapRecord(KIND_VARS, FALLBACK.kind, styles),
    proof: mapRecord(PROOF_VARS, FALLBACK.proof, styles),
    age: mapRecord(AGE_VARS, FALLBACK.age, styles),
    dispute: readVar(styles, DISPUTE_VAR, FALLBACK.dispute),
  };
}

/** The palette `memoryPalette` falls back to when a token can't be resolved. */
export const DEFAULT_MEMORY_PALETTE: MemoryPalette = FALLBACK;
