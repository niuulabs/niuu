/**
 * RavnSubnav — all Ravn pages now own their own left-column layouts.
 */

import { useRouterState } from '@tanstack/react-router';

export function RavnSubnav() {
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  void location;
  return null;
}
