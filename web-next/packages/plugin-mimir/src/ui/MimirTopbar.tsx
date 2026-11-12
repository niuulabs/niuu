/**
 * MimirTopbar — shell topbar-right slot for the Mímir plugin.
 *
 * Shows: active mount name · pages count,
 * on every Mímir route except the memory view.
 */

import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { useRouterState } from '@tanstack/react-router';
import { useActiveMount } from '../application/useActiveMount';
import { useMimirPages } from './useMimirPages';
import './MimirTopbar.css';

interface MimirTopbarProps {
  ctx: PluginCtx;
}

/**
 * The memory view (`/mimir`) carries its own counts in its panels, drawn from
 * the graph it shows; the registry routes keep these instance stats.
 */
export function MimirTopbar({ ctx: _ctx }: MimirTopbarProps) {
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  if (pathname === '/mimir') return null;
  return <MimirTopbarStats />;
}

function MimirTopbarStats() {
  const { activeMount, mountName } = useActiveMount();

  const { data: pages = [] } = useMimirPages(mountName ? { mountName } : undefined);

  const mountLabel = activeMount === 'all' ? 'all mounts' : activeMount;
  const pageCount =
    activeMount === 'all'
      ? pages.length
      : pages.filter((p) => p.mounts.includes(activeMount)).length;

  return (
    <div className="mm-topbar-stats" aria-label="Memory stats">
      <span className="mm-topbar-stat">
        <span className="mm-topbar-stat__k">mount</span>
        <strong className="mm-topbar-stat__v">{mountLabel}</strong>
      </span>
      <span className="mm-topbar-sep" aria-hidden>
        ·
      </span>
      <span className="mm-topbar-stat">
        <span className="mm-topbar-stat__k">pages</span>
        <strong className="mm-topbar-stat__v">{pageCount.toLocaleString()}</strong>
      </span>
    </div>
  );
}
