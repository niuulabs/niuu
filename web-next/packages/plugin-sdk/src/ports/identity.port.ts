/**
 * Identity port — IDP-agnostic interface for reading the current user's identity.
 *
 * Consumers import from here instead of coupling to any specific service or IDP.
 * When the identity endpoint changes, only the adapter needs to update.
 */

export interface AppIdentity {
  userId: string;
  email: string;
  tenantId: string;
  roles: string[];
  displayName: string;
  status: string;
}

export interface IIdentityService {
  getIdentity(): Promise<AppIdentity>;
}
