import {
  useEffect,
  useLayoutEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
  type ReactNode,
} from 'react';
import type { User } from 'oidc-client-ts';
import { useConfig } from '@niuulabs/plugin-sdk';
import { setTokenProvider } from '@niuulabs/query';
import { getOidcConfig, getUserManager, type OidcConfig } from './oidc';
import { AuthContext, type AuthContextValue } from './AuthContext';
import styles from './AuthProvider.module.css';

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
          const callbackUser = await mgr.signinRedirectCallback();
          if (!cancelled) setUser(callbackUser);
          window.history.replaceState({}, document.title, window.location.pathname);
          if (!cancelled) setLoading(false);
          return;
        }

        const existingUser = await mgr.getUser();
        if (!cancelled && existingUser && !existingUser.expired) {
          setUser(existingUser);
        }
      } catch (err) {
        console.error('OIDC init error:', err);
      }
      if (!cancelled) setLoading(false);
    }

    const handleUserLoaded = (loadedUser: User) => setUser(loadedUser);
    const handleUserUnloaded = () => setUser(null);
    const handleTokenExpired = () => {
      mgr.signinSilent().catch(() => setUser(null));
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
