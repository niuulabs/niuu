import type { Registry, EntityType } from './index';

/**
 * Returns true if `descendantId` appears anywhere beneath `ancestorId` in the
 * canContain DAG (depth-first, cycle-safe via visited set).
 *
 * Also returns true when ancestorId === descendantId, so callers can use this
 * as the single "would this drop create a cycle?" guard.
 *
 * Pass a pre-built `byId` map (e.g. from a memoized value) to avoid
 * rebuilding it on every call — important during drag operations.
 */
export function isDescendant(
  registry: Registry,
  ancestorId: string,
  descendantId: string,
  byId?: Map<string, EntityType>,
): boolean {
  if (ancestorId === descendantId) return true;

  const lookup = byId ?? new Map(registry.types.map((t) => [t.id, t]));
  const seen = new Set<string>();

  const walk = (id: string): boolean => {
    if (seen.has(id)) return false;
    seen.add(id);
    const t = lookup.get(id);
    if (!t) return false;
    for (const childId of t.canContain) {
      if (childId === descendantId) return true;
      if (walk(childId)) return true;
    }
    return false;
  };

  return walk(ancestorId);
}

/**
 * Returns a new Registry with `childId` reparented under `newParentId`.
 *
 * Mutations applied immutably:
 * 1. Remove `childId` from every old parent's `canContain` (except newParentId).
 * 2. Add `childId` to `newParentId`'s `canContain` (if not already there).
 * 3. Rewrite the child's `parentTypes` to `[newParentId]` (single-parent model).
 * 4. Bump `version` and set `updatedAt` to now.
 *
 * Pre-condition: caller must verify the drop is valid (not a cycle, not self).
 */
export function reparent(registry: Registry, childId: string, newParentId: string): Registry {
  const types = registry.types.map((t) => {
    // Remove child from any previous parent's canContain (except the new parent).
    if (t.canContain.includes(childId) && t.id !== newParentId) {
      return { ...t, canContain: t.canContain.filter((id) => id !== childId) };
    }
    // Add child to new parent's canContain.
    if (t.id === newParentId && !t.canContain.includes(childId)) {
      return { ...t, canContain: [...t.canContain, childId] };
    }
    // Rewrite child's parentTypes to single-parent.
    if (t.id === childId) {
      return { ...t, parentTypes: [newParentId] };
    }
    return t;
  });

  return {
    ...registry,
    types,
    version: registry.version + 1,
    updatedAt: new Date().toISOString(),
  };
}
