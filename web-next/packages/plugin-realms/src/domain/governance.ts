/**
 * Types mirror the backend REST casing exactly (snake_case), served by
 * /api/v1/realms.
 */

/** Realm as returned by GET /api/v1/realms. */
export interface RealmSummary {
  id: string;
  slug: string;
  name: string;
  sleipnir_domain: string | null;
  owner_id: string | null;
  instance_id: string | null;
  autonomy_profile: string;
  /** The Forge project this realm belongs to. */
  project_id?: string | null;
  created_at: string;
  updated_at: string;
}

/** Trust grant as returned by GET /api/v1/realms/{slug}/trust-grants. */
export interface RealmTrustGrant {
  id: string;
  realm_id: string;
  action_class: string;
  target: string;
  level: number;
  limits: Record<string, unknown>;
  granted_by: string | null;
  granted_at: string;
}

/** Capability as returned in a realm-detail stream snapshot. */
export interface RealmCapability {
  id: string;
  realm_id: string;
  name: string;
  kind: string;
  status: string;
  trust_level: number;
  mimir_page_path: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

/** Complete event payload for ``realm.detail`` SSE frames. */
export interface RealmDetail {
  realm: RealmSummary;
  trust_grants: RealmTrustGrant[];
  capabilities: RealmCapability[];
}
