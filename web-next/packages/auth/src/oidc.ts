import { UserManager, WebStorageStateStore, type UserManagerSettings } from 'oidc-client-ts';
import type { NiuuConfig } from '@niuulabs/plugin-sdk';

export interface OidcConfig {
  authority: string;
  clientId: string;
  redirectUri: string;
  postLogoutRedirectUri: string;
  scope: string;
}

/**
 * Build OIDC configuration from the runtime NiuuConfig.
 * Returns null when OIDC is not configured (dev / allow-all mode).
 */
export function getOidcConfig(config: NiuuConfig): OidcConfig | null {
  const auth = config.auth;
  if (!auth?.issuer || !auth?.clientId) {
    return null;
  }

  return {
    authority: auth.issuer,
    clientId: auth.clientId,
    redirectUri: `${window.location.origin}/login/callback`,
    postLogoutRedirectUri: window.location.origin,
    scope: 'openid profile email',
  };
}

let userManagerInstance: UserManager | null = null;

/**
 * Create (or return cached) UserManager for the given OIDC config.
 */
export function getUserManager(config: OidcConfig): UserManager {
  if (userManagerInstance) {
    return userManagerInstance;
  }

  const settings: UserManagerSettings = {
    authority: config.authority,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    post_logout_redirect_uri: config.postLogoutRedirectUri,
    scope: config.scope,
    response_type: 'code',
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  };

  userManagerInstance = new UserManager(settings);
  return userManagerInstance;
}

/**
 * Reset the cached UserManager. Used in tests to ensure isolation.
 */
export function resetUserManager(): void {
  userManagerInstance = null;
}
