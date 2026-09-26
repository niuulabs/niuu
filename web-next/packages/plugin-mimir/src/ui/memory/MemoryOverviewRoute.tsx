/**
 * MemoryOverviewRoute — `/mimir` in whichever mode the operator chose.
 *
 * Simple mode gets the memory home (a flat, card-based "what does Niuu
 * remember?" dashboard). Advanced mode gets the navigable Memory Explore
 * scene (Explore/Focus/Replay) — this replaces the old Overview/Pages/
 * Sources/Search/Graph tab set as Mímir's home for operators. The route is
 * the same either way, so a link works from both.
 */

import { useUiMode } from '@niuulabs/shell';
import { MemoryExploreView } from './MemoryExploreView';
import { MemoryHomePage } from './MemoryHomePage';

export function MemoryOverviewRoute() {
  const mode = useUiMode();
  if (mode === 'simple') return <MemoryHomePage />;
  return <MemoryExploreView />;
}
