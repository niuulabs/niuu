/**
 * MemoryOverviewRoute — `/mimir` in whichever mode the operator chose.
 *
 * Simple mode gets the memory home; Advanced keeps the operator overview. The
 * route is the same either way, so a link works from both.
 */

import { useUiMode } from '@niuulabs/shell';
import { MimirPage } from '../MimirPage';
import { MemoryHomePage } from './MemoryHomePage';

export function MemoryOverviewRoute() {
  const mode = useUiMode();
  if (mode === 'simple') return <MemoryHomePage />;
  return <MimirPage />;
}
