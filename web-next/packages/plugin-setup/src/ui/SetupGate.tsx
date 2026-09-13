import { useEffect, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { setupKeys, useOptionalSetupService } from './hooks';

export const SETUP_PATH = '/setup';
export const READY_PATH = '/ready';

export function isSetupPath(pathname: string): boolean {
  return (
    pathname === SETUP_PATH || pathname.startsWith(`${SETUP_PATH}/`) || pathname === READY_PATH
  );
}

export interface SetupGateProps {
  children: ReactNode;
  /** Current location; defaults to `window.location.pathname`. */
  pathname?: string;
  /** Redirect; defaults to `window.location.replace`. */
  redirect?: (path: string) => void;
}

/**
 * Sends a not-yet-configured single-host install to the wizard.
 *
 * Inert when no setup service is registered, when the platform reports the
 * wizard disabled, or while the state is unknown. It renders children in the
 * meantime rather than blocking the app on one request.
 */
export function SetupGate({ children, pathname, redirect }: SetupGateProps) {
  const service = useOptionalSetupService();
  const location = pathname ?? (typeof window !== 'undefined' ? window.location.pathname : '/');
  const query = useQuery({
    queryKey: setupKeys.state,
    queryFn: () => service!.getState(),
    enabled: service !== undefined,
    retry: false,
  });
  const mustRedirect = Boolean(
    service && query.data && query.data.enabled && !query.data.completed && !isSetupPath(location),
  );

  useEffect(() => {
    if (!mustRedirect) return;
    if (redirect) {
      redirect(SETUP_PATH);
      return;
    }
    // Keep the query string so dev-only switches (e.g. ?config=default) survive.
    window.location.replace(`${SETUP_PATH}${window.location.search}`);
  }, [mustRedirect, redirect]);

  if (mustRedirect) return null;
  return <>{children}</>;
}
