import { useEffect, type ReactNode } from 'react';
import { useAuth } from './useAuth';

interface RequireAuthProps {
  children: ReactNode;
  loginPath?: string;
}

/**
 * Route guard that redirects unauthenticated users to the login path.
 * When auth is disabled (dev / allow-all mode) children are always rendered.
 */
export function RequireAuth({ children, loginPath = '/login' }: RequireAuthProps) {
  const { enabled, authenticated, loading } = useAuth();
  const needsRedirect = !loading && enabled && !authenticated;

  useEffect(() => {
    if (needsRedirect) {
      window.location.replace(loginPath);
    }
  }, [needsRedirect, loginPath]);

  if (loading || needsRedirect) {
    return null;
  }

  return <>{children}</>;
}
