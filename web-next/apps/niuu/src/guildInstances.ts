import type { AppIdentity } from '@niuulabs/plugin-sdk';

export type InstanceKind =
  'volundr' | 'ting' | 'mimir' | 'bifrost' | 'ravn' | 'observatory' | 'generic';
export type VisibilityScope = 'user' | 'tenant' | 'system';
export type InstanceHealth = 'unknown' | 'ok' | 'unreachable';

export type InstanceRecord = {
  id: string;
  kind: InstanceKind;
  slug: string;
  name: string;
  baseUrl: string;
  visibility: VisibilityScope | string;
  ownerId: string | null;
  tenantId: string | null;
  enabled: boolean;
  isDefault: boolean;
  config: Record<string, unknown>;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  /** Server-recorded reachability — probed on register and on a periodic loop. */
  health: InstanceHealth;
  /** Last time a probe SUCCEEDED — never invented for a node that hasn't. */
  lastSeenAt: string | null;
  /** Last time a probe was ATTEMPTED, success or not — "when did we last look". */
  lastCheckedAt: string | null;
  lastError: string | null;
};

const KNOWN_INSTANCE_HEALTH_VALUES: readonly InstanceHealth[] = ['unknown', 'ok', 'unreachable'];

/**
 * Coerce a `health` value to a known InstanceHealth, defaulting to
 * 'unknown' for anything else — including `undefined`/`null` from an older
 * backend or a test fixture that predates this field, and any future value
 * this build doesn't recognize yet. Without this, an unrecognized value
 * would fail every `=== 'ok'` / `=== 'unreachable'` check silently, but
 * could still slip through a loose comparison and render as unreachable by
 * accident.
 */
export function normalizeHealth(value: unknown): InstanceHealth {
  return KNOWN_INSTANCE_HEALTH_VALUES.includes(value as InstanceHealth)
    ? (value as InstanceHealth)
    : 'unknown';
}

export type InstanceUpdate = Pick<
  InstanceRecord,
  'name' | 'slug' | 'baseUrl' | 'enabled' | 'isDefault' | 'tags' | 'config'
> & { visibility?: VisibilityScope };

/** Mirrors the registry's management policy; the API still enforces authorization. */
export function canManageInstance(instance: InstanceRecord, identity?: AppIdentity): boolean {
  if (!identity) return false;
  if (identity.roles.includes('volundr:admin')) return true;
  if (instance.visibility === 'user') return instance.ownerId === identity.userId;
  return (
    instance.visibility === 'tenant' &&
    Boolean(identity.tenantId) &&
    instance.tenantId === identity.tenantId
  );
}

export function parseTags(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[,\s]+/)
        .map((tag) => tag.trim())
        .filter(Boolean),
    ),
  ];
}

export function registryError(error: unknown): string {
  if (error && typeof error === 'object' && 'detail' in error && typeof error.detail === 'string')
    return error.detail;
  return error instanceof Error ? error.message : 'The registry request failed. Please try again.';
}
