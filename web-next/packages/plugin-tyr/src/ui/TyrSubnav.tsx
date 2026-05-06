import { useRouterState } from '@tanstack/react-router';
import { SettingsRail } from './settings/SettingsRail';

/**
 * Route-aware subnav for Tyr. Only renders the SettingsRail when the user
 * is on a /tyr/settings/* route. Returns null for all other Tyr routes so
 * the Shell collapses the subnav column.
 */
export function TyrSubnav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if (!pathname.startsWith('/tyr/settings')) {
    return null;
  }

  return <SettingsRail />;
}
