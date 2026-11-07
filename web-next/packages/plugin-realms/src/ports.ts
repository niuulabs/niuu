import type { RealmDetail, RealmSummary, RealmTrustGrant } from './domain/governance';

/** Body for POST /api/v1/realms/{slug}/trust-grants (exact backend casing). */
export interface TrustGrantCreate {
  action_class: string;
  target: string;
  level: number;
  limits: Record<string, unknown>;
  granted_by?: string | null;
}

/** Body for POST /api/v1/realms (exact backend casing). */
export interface RealmCreate {
  slug: string;
  name: string;
  sleipnir_domain?: string | null;
  owner_id?: string | null;
  instance_id?: string | null;
  autonomy_profile?: string;
  project_id?: string | null;
}

export type RealmDetailListener = (detail: RealmDetail) => void;

export interface IRealmGovernanceService {
  listRealms(): Promise<RealmSummary[]>;
  getRealm(slug: string): Promise<RealmSummary>;
  /** Opens the authenticated ``realm.detail`` SSE channel for one realm. */
  subscribeRealmDetail(slug: string, listener: RealmDetailListener): () => void;
  createRealm(request: RealmCreate): Promise<RealmSummary>;
  /** Moves the realm into a project, or out of one with null. */
  setRealmProject(slug: string, projectId: string | null): Promise<RealmSummary>;
  /** Removes the realm with its trust grants and capabilities. */
  deleteRealm(slug: string): Promise<void>;
  listTrustGrants(slug: string): Promise<RealmTrustGrant[]>;
  createTrustGrant(slug: string, request: TrustGrantCreate): Promise<RealmTrustGrant>;
}
