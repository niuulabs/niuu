import type { AppIdentity } from '@niuulabs/plugin-sdk';

export type InstanceKind =
  'volundr' | 'ting' | 'mimir' | 'bifrost' | 'ravn' | 'observatory' | 'generic';
export type VisibilityScope = 'user' | 'tenant' | 'system';
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
};

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
