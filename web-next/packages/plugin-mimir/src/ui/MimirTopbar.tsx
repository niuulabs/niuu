/**
 * MimirTopbar — shell topbar-right slot for the Mímir plugin.
 *
 * Shows: active mount name · pages count · wardens count · lint count (red if > 0)
 */

import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { useMimirPages } from './useMimirPages';
import { useLint } from '../application/useLint';
import { useRavns } from '../application/useRavns';
import './MimirTopbar.css';

interface MimirTopbarProps {
  ctx: PluginCtx;
}

export function MimirTopbar({ ctx }: MimirTopbarProps) {
  const activeMount = (ctx.tweaks.activeMount as string | undefined) ?? 'all';

  const { data: pages = [] } = useMimirPages();
  const { summary: lintSummary } = useLint();
  const { data: ravns = [] } = useRavns();

  const mountLabel = activeMount === 'all' ? 'all mounts' : activeMount;
  const pageCount =
    activeMount === 'all'
      ? pages.length
      : pages.filter((p) => p.mounts.includes(activeMount)).length;
  const ravnCount =
    activeMount === 'all'
      ? ravns.length
      : ravns.filter((r) => r.mountNames.includes(activeMount)).length;
  const lintCount = lintSummary.error + lintSummary.warn + lintSummary.info;

  return (
    <div className="mm-topbar-stats" aria-label="Mímir stats">
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
      <span className="mm-topbar-sep" aria-hidden>
        ·
      </span>
      <span className="mm-topbar-stat">
        <span className="mm-topbar-stat__k">wardens</span>
        <strong className="mm-topbar-stat__v">{ravnCount}</strong>
      </span>
      <span className="mm-topbar-sep" aria-hidden>
        ·
      </span>
      <span className="mm-topbar-stat">
        <span className="mm-topbar-stat__k">lint</span>
        <strong
          className={`mm-topbar-stat__v${lintCount > 0 ? ' mm-topbar-stat__v--warn' : ''}`}
          aria-label={`${lintCount} lint issues`}
        >
          {lintCount}
        </strong>
      </span>
    </div>
  );
}
