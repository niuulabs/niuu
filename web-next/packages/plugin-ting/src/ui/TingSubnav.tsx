import { useRouterState } from '@tanstack/react-router';
import { SettingsRail } from './settings/SettingsRail';

/**
 * Route-aware subnav for Ting. Only renders the SettingsRail when the user
 * is on a /ting/settings/* route. Returns null for all other Ting routes so
 * the Shell collapses the subnav column.
 */
export function TingSubnav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if (!pathname.startsWith('/ting/settings')) {
    return null;
  }

  return <SettingsRail />;
}
