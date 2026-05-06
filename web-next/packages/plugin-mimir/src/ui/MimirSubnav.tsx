/**
 * MimirSubnav — shell subnav slot for the Mímir plugin.
 *
 * Sections:
 *   1. Mount picker — "all mounts" + per-mount rows with status dots
 *   2. Quick filters — Errors / Flagged / Low confidence
 *   3. Wardens roster — top-6 ravns with initials + state dot
 *
 * Navigation tabs live in the topbar (plugin descriptor `tabs` array).
 */

import { useNavigate } from '@tanstack/react-router';
import { StateDot } from '@niuulabs/ui';
import type { PluginCtx } from '@niuulabs/plugin-sdk';
import { useMimirMounts } from './useMimirMounts';
import { useMimirPages } from './useMimirPages';
import { useLint } from '../application/useLint';
import { useRavns } from '../application/useRavns';
import { RAVN_DOT_STATE, MOUNT_DOT_STATE } from './mimir.constants';
import './MimirSubnav.css';

interface MimirSubnavProps {
  ctx: PluginCtx;
}

export function MimirSubnav({ ctx }: MimirSubnavProps) {
  const navigate = useNavigate();

  const activeMount = (ctx.tweaks.activeMount as string | undefined) ?? 'all';
  const setActiveMount = (m: string) => ctx.setTweak('activeMount', m);

  const { data: mounts = [] } = useMimirMounts();
  const { data: pages = [] } = useMimirPages();
  const { summary: lintSummary } = useLint();
  const { data: ravns = [] } = useRavns();

  const errorCount = lintSummary.error;
  const flaggedCount = pages.filter((p) => p.flagged).length;
  const lowConfidenceCount = pages.filter((p) => p.confidence === 'low').length;

  return (
    <nav className="mm-subnav" aria-label="Mímir navigation">
      {/* ── Mount picker ─────────────────────────────────────────── */}
      <div className="mm-subnav-block">
        <div className="mm-subnav-label">Mount focus</div>
        <div className="mm-mount-picker">
          <button
            type="button"
            className={`mm-mount-row${activeMount === 'all' ? ' mm-mount-row--active' : ''}`}
            onClick={() => setActiveMount('all')}
            aria-pressed={activeMount === 'all'}
          >
            <span className="mm-mount-row__name">All mounts</span>
            <span className="mm-mount-row__count">{mounts.length}</span>
          </button>
          {mounts.map((m) => (
            <button
              key={m.name}
              type="button"
              className={[
                'mm-mount-row',
                activeMount === m.name ? 'mm-mount-row--active' : '',
                m.status === 'down' ? 'mm-mount-row--muted' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              onClick={() => setActiveMount(m.name)}
              aria-pressed={activeMount === m.name}
            >
              <StateDot state={MOUNT_DOT_STATE[m.status]} size={6} />
              <span className="mm-mount-row__name">{m.name}</span>
              <span className="mm-mount-row__role">{m.role}</span>
            </button>
          ))}
        </div>
      </div>

      {/* ── Quick filters ─────────────────────────────────────────── */}
      <div className="mm-subnav-block">
        <div className="mm-subnav-label">Quick filters</div>
        <button
          type="button"
          className="mm-subnav-btn"
          onClick={() => navigate({ to: '/mimir/lint' })}
          aria-label={`${errorCount} lint errors`}
        >
          <span className="mm-subnav-btn__glyph mm-subnav-btn__glyph--err" aria-hidden>
            ●
          </span>
          <span className="mm-subnav-btn__label">Errors</span>
          <span className="mm-subnav-btn__count mm-subnav-btn__count--red">{errorCount}</span>
        </button>
        <button
          type="button"
          className="mm-subnav-btn"
          onClick={() => navigate({ to: '/mimir/pages' })}
          aria-label={`${flaggedCount} flagged pages`}
        >
          <span className="mm-subnav-btn__glyph mm-subnav-btn__glyph--warn" aria-hidden>
            ●
          </span>
          <span className="mm-subnav-btn__label">Flagged</span>
          <span className="mm-subnav-btn__count">{flaggedCount}</span>
        </button>
        <button
          type="button"
          className="mm-subnav-btn"
          onClick={() => navigate({ to: '/mimir/pages' })}
          aria-label={`${lowConfidenceCount} low confidence pages`}
        >
          <span className="mm-subnav-btn__glyph mm-subnav-btn__glyph--dim" aria-hidden>
            ◇
          </span>
          <span className="mm-subnav-btn__label">Low confidence</span>
          <span className="mm-subnav-btn__count">{lowConfidenceCount}</span>
        </button>
      </div>

      {/* ── Wardens roster ────────────────────────────────────────── */}
      {ravns.length > 0 && (
        <div className="mm-subnav-block">
          <div className="mm-subnav-label">Wardens</div>
          {ravns.slice(0, 6).map((ravn) => (
            <button
              key={ravn.ravnId}
              type="button"
              className="mm-subnav-btn"
              onClick={() => navigate({ to: '/mimir/ravns' })}
              aria-label={`Warden ${ravn.ravnId}`}
            >
              <span className="mm-subnav-btn__initials" aria-hidden>
                {ravn.ravnId.charAt(0)}
                {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
              </span>
              <span className="mm-subnav-btn__label">{ravn.ravnId}</span>
              <StateDot state={RAVN_DOT_STATE[ravn.state]} size={6} />
            </button>
          ))}
        </div>
      )}
    </nav>
  );
}
