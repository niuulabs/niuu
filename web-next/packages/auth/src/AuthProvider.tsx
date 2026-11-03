import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
  type ReactNode,
} from 'react';
import type { User, UserManager } from 'oidc-client-ts';
import { useConfig } from '@niuulabs/plugin-sdk';
import { setTokenProvider } from '@niuulabs/query';
import { getOidcConfig, getUserManager, type OidcConfig } from './oidc';
import { AuthContext, type AuthContextValue } from './AuthContext';
import styles from './AuthProvider.module.css';

/**
 * Renew with the refresh token. A failure means the identity provider ended the
 * session (idle or max lifetime, or a sign-in elsewhere replaced it), so the user
 * has to sign in again; say why once, and drop the dead session so the next page
 * load does not try it again.
 */
async function renew(mgr: UserManager, purpose: string): Promise<User | null> {
  try {
    return await mgr.signinSilent();
  } catch (err) {
    console.error(`OIDC: ${purpose} failed; the user must sign in again.`, err);
    await mgr.removeUser();
    return null;
  }
}

/**
 * Exchange each authorization code once. Keycloak treats a second exchange of the
 * same code as a replay and revokes the session the first one created, so the
 * next token refresh fails with "Session doesn't have required client". React
 * runs effects twice in development, which would otherwise exchange it twice.
 */
const signinCallbacks = new Map<string, Promise<User>>();

function completeSignin(mgr: UserManager, callbackSearch: string): Promise<User> {
  const pending = signinCallbacks.get(callbackSearch);
  if (pending) return pending;
  const exchange = mgr.signinRedirectCallback();
  signinCallbacks.set(callbackSearch, exchange);
  return exchange;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const config = useConfig();
  const oidcConfig: OidcConfig | null = useMemo(() => getOidcConfig(config), [config]);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(oidcConfig !== null);
  const userRef = useRef<User | null>(null);

  const enabled = oidcConfig !== null;

  // Keep ref in sync so the token provider closure always reads the latest token.
  useLayoutEffect(() => {
    userRef.current = user;
  }, [user]);

  // Register token provider synchronously before children's useEffect calls.
  useLayoutEffect(() => {
    if (!enabled) return;
    setTokenProvider(() => userRef.current?.access_token ?? null);
    return () => setTokenProvider(null);
  }, [enabled]);

  const login = useCallback(() => {
    if (!oidcConfig) return;
    const mgr = getUserManager(oidcConfig);
    mgr.signinRedirect();
  }, [oidcConfig]);

  const logout = useCallback(() => {
    if (!oidcConfig) return;
    const mgr = getUserManager(oidcConfig);
    mgr.signoutRedirect();
  }, [oidcConfig]);

  useEffect(() => {
    if (!oidcConfig) {
      return;
    }

    const mgr = getUserManager(oidcConfig);
    let cancelled = false;

    async function init() {
      try {
        const params = new URLSearchParams(window.location.search);
        if (params.has('code') || params.has('error')) {
          const callbackUser = await completeSignin(mgr, window.location.search);
          if (!cancelled) setUser(callbackUser);
          window.history.replaceState({}, document.title, window.location.pathname);
          if (!cancelled) setLoading(false);
          return;
        }

        const existingUser = await mgr.getUser();
        if (!cancelled && existingUser && !existingUser.expired) {
          setUser(existingUser);
        }
        // An access token lapses while the tab sleeps or is hidden, but its
        // refresh token outlives it: renew instead of sending the user to sign in.
        if (existingUser?.expired && existingUser.refresh_token) {
          const renewed = await renew(mgr, 'restoring the stored session');
          if (!cancelled && renewed) setUser(renewed);
        }
      } catch (err) {
        console.error('OIDC init error:', err);
      }
      if (!cancelled) setLoading(false);
    }

    const handleUserLoaded = (loadedUser: User) => setUser(loadedUser);
    const handleUserUnloaded = () => setUser(null);
    const handleTokenExpired = async () => {
      const renewed = await renew(mgr, 'renewing the expired access token');
      if (!renewed) setUser(null);
    };

    mgr.events.addUserLoaded(handleUserLoaded);
    mgr.events.addUserUnloaded(handleUserUnloaded);
    mgr.events.addAccessTokenExpired(handleTokenExpired);

    init();

    return () => {
      cancelled = true;
      mgr.events.removeUserLoaded(handleUserLoaded);
      mgr.events.removeUserUnloaded(handleUserUnloaded);
      mgr.events.removeAccessTokenExpired(handleTokenExpired);
    };
  }, [oidcConfig]);

  const value: AuthContextValue = useMemo(
    () => ({
      enabled,
      authenticated: enabled ? user !== null && !user.expired : true,
      loading,
      user,
      accessToken: user?.access_token ?? null,
      login,
      logout,
    }),
    [enabled, user, loading, login, logout],
  );

  if (loading) {
    return (
      <div className={styles.loadingPage}>
        <div className={styles.loadingSpinner} />
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
