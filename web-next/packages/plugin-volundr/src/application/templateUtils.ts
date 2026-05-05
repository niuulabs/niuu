import type { PodSpec } from '../domain/pod';
import type { Template } from '../domain/template';

/**
 * Masks env keys that are listed in envSecretRefs, replacing their
 * values with '***' so they are safe to display in the UI.
 */
export function maskSecretRefs(
  env: Record<string, string>,
  secretRefs: string[],
): Record<string, string> {
  const refs = new Set(secretRefs);
  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(env)) {
    result[key] = refs.has(key) ? '***' : value;
  }
  return result;
}

/**
 * Returns a shallow copy of the source template's spec suitable for cloning.
 */
export function buildCloneSpec(source: Template): PodSpec {
  return { ...source.spec };
}

/**
 * Returns the display name for a cloned template.
 */
export function cloneName(original: string): string {
  return `Clone of ${original}`;
}
