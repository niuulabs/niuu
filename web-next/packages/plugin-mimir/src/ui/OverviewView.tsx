/**
 * OverviewView — Mímir landing screen.
 *
 * Layout:
 *   KPI strip — aggregate pages / sources / wardens / lint / last write
 *   2-col grid (1.2fr 1fr, border-right separator):
 *     LEFT:  mount cards grid (minmax 300px)
 *     RIGHT: activity feed (time / kind / mount / message)
 *
 * Mount cards are expandable — clicking reveals role, size, category
 * badges, and a 5-item per-mount recent activity excerpt. Warden detail
 * lives on the Wardens tab; the overview only surfaces the count.
 */

import { useState } from 'react';
import { KpiStrip, KpiCard, StateDot, relTime } from '@niuulabs/ui';
import { useMimirMounts } from './useMimirMounts';
import { useMimirRecentWrites } from './useMimirSources';
import { useRavns } from '../application/useRavns';
import { MountChip } from './components/MountChip';
import { MOUNT_DOT_STATE } from './mimir.constants';

const FEED_LIMIT = 20;
const MOUNT_ACTIVITY_LIMIT = 5;
const TIMESTAMP_HOUR_START = 11;
const TIMESTAMP_HOUR_END = 16;

/** HH:MM slice from ISO timestamp — used in feed row and mount activity excerpt. */
function formatTimestamp(iso: string): string {
  return iso.slice(TIMESTAMP_HOUR_START, TIMESTAMP_HOUR_END);
}

/** Tailwind color class per feed entry kind. Falls back to text-secondary. */
const FEED_KIND_COLOR: Record<string, string> = {
  write: 'niuu:text-brand-300',
  compile: 'niuu:text-brand',
  'lint-fix': 'niuu:text-brand-200',
  dream: 'niuu:text-brand',
};

export function OverviewView() {
  const [expandedMount, setExpandedMount] = useState<string | null>(null);
  const { data: mounts, isLoading: mountsLoading, error: mountsError } = useMimirMounts();
  const { data: feed } = useMimirRecentWrites(FEED_LIMIT);
  // Warden detail lives on the Wardens tab (and the context rail); the
  // overview keeps only the KPI count to avoid triplicating the roster.
  const { data: ravns = [] } = useRavns();

  const totalPages = mounts?.reduce((a, m) => a + m.pages, 0) ?? 0;
  const totalSources = mounts?.reduce((a, m) => a + m.sources, 0) ?? 0;
  const totalLint = mounts?.reduce((a, m) => a + m.lintIssues, 0) ?? 0;
  const lastWrite = mounts
    ? mounts.reduce((latest, m) => (m.lastWrite > latest ? m.lastWrite : latest), '')
    : '';

  if (mountsLoading) {
    return (
      <div className="niuu:p-6">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <StateDot state="processing" pulse />
          <span className="niuu:text-sm niuu:text-text-secondary">loading…</span>
        </div>
      </div>
    );
  }

  if (mountsError) {
    return (
      <div className="niuu:p-6">
        <div className="niuu:text-xs niuu:text-critical niuu:bg-critical-bg niuu:border niuu:border-critical-bo niuu:rounded-sm niuu:px-4 niuu:py-2">
          {mountsError instanceof Error ? mountsError.message : String(mountsError)}
        </div>
      </div>
    );
  }

  return (
    <div className="niuu:flex niuu:flex-col">
      {/* ── KPI strip ─────────────────────────────────────────────── */}
      <KpiStrip>
        <KpiCard label="pages" value={totalPages.toLocaleString()} />
        <KpiCard label="sources" value={totalSources.toLocaleString()} deltaLabel="raw ingested" />
        <KpiCard label="wardens" value={ravns.length} deltaLabel="ravns bound to mounts" />
        <KpiCard
          label="lint issues"
          value={totalLint}
          delta={totalLint > 0 ? `${totalLint} open` : 'clean'}
          deltaTrend="neutral"
          className={totalLint > 0 ? 'mm-kpi--warn' : undefined}
        />
        <KpiCard
          label="last write"
          value={lastWrite ? relTime(lastWrite) : '—'}
          deltaLabel="newest across mounts"
        />
      </KpiStrip>

      {/* ── 2-column home layout ──────────────────────────────────── */}
      <div className="niuu:grid niuu:grid-cols-[1.2fr_1fr] niuu:flex-1 niuu:min-h-0">
        {/* LEFT column: mount grid + wardens */}
        <div className="niuu:p-5 niuu:overflow-y-auto niuu:border-r niuu:border-border-subtle">
          {/* ── Mounts section head ────────────────────────────────── */}
          <div className="niuu:flex niuu:items-baseline niuu:gap-3 niuu:mb-4">
            <h3 className="niuu:m-0 niuu:text-base niuu:text-text-primary">Mounts</h3>
            <span className="niuu:text-xs niuu:text-text-muted">
              {mounts?.length ?? 0} instance{(mounts?.length ?? 0) !== 1 ? 's' : ''} connected ·
              click to expand
            </span>
          </div>

          {/* ── Mount cards grid ───────────────────────────────────── */}
          <div className="niuu:grid niuu:gap-3 niuu:grid-cols-2 niuu:mb-5">
            {mounts?.map((mount) => {
              const isExpanded = expandedMount === mount.name;
              const mountFeed =
                feed?.filter((e) => e.mount === mount.name).slice(0, MOUNT_ACTIVITY_LIMIT) ?? [];

              return (
                <article
                  key={mount.name}
                  className="niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:p-4 niuu:cursor-pointer"
                  aria-label={`mount ${mount.name}`}
                  aria-expanded={isExpanded}
                  onClick={() => setExpandedMount(isExpanded ? null : mount.name)}
                >
                  {/* Card head */}
                  <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:mb-2">
                    <StateDot state={MOUNT_DOT_STATE[mount.status]} />
                    <span className="niuu:text-sm niuu:font-semibold niuu:text-text-primary niuu:flex-1">
                      {mount.name}
                    </span>
                    <MountChip name={mount.name} role={mount.role} />
                  </div>
                  <div className="niuu:text-xs niuu:font-mono niuu:text-text-muted niuu:mb-2">
                    {mount.host}
                  </div>
                  <div className="niuu:text-xs niuu:text-text-secondary niuu:mb-3">
                    {mount.desc}
                  </div>

                  {/* Metrics row */}
                  <div className="niuu:flex niuu:gap-3 niuu:flex-wrap">
                    <span className="niuu:font-mono niuu:text-xs niuu:text-text-secondary">
                      <strong className="niuu:text-text-primary">{mount.pages}</strong> pages
                    </span>
                    <span className="niuu:font-mono niuu:text-xs niuu:text-text-secondary">
                      <strong className="niuu:text-text-primary">{mount.sources}</strong> sources
                    </span>
                    <span className="niuu:font-mono niuu:text-xs niuu:text-text-secondary">
                      <strong
                        className={
                          mount.lintIssues > 10 ? 'niuu:text-brand-400' : 'niuu:text-text-primary'
                        }
                      >
                        {mount.lintIssues}
                      </strong>{' '}
                      lint
                    </span>
                    <span className="niuu:font-mono niuu:text-xs niuu:text-text-secondary">
                      <strong className="niuu:text-text-primary">
                        {(mount.sizeKb / 1024).toFixed(1)}
                      </strong>{' '}
                      MB
                    </span>
                  </div>

                  {mount.categories && (
                    <div className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">
                      scope:{' '}
                      <span className="niuu:text-brand-300">{mount.categories.join(', ')}</span>
                    </div>
                  )}

                  {/* ── Expanded detail panel ──────────────────────── */}
                  {isExpanded && (
                    <div className="niuu:mt-4 niuu:pt-4 niuu:border-t niuu:border-border-subtle">
                      {/* Config detail grid — host already visible above, not repeated */}
                      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:mb-3 niuu:font-mono niuu:text-xs">
                        <div>
                          <div className="niuu:text-text-muted">role</div>
                          <div className="niuu:text-text-primary">{mount.role}</div>
                        </div>
                        <div>
                          <div className="niuu:text-text-muted">size</div>
                          <div className="niuu:text-text-primary">
                            {(mount.sizeKb / 1024).toFixed(2)} MB
                          </div>
                        </div>
                        {mount.categories && (
                          <div>
                            <div className="niuu:text-text-muted">categories</div>
                            <div className="niuu:flex niuu:flex-wrap niuu:gap-1 niuu:mt-1">
                              {mount.categories.map((cat) => (
                                <span
                                  key={cat}
                                  className="niuu:bg-bg-tertiary niuu:border niuu:border-border-subtle niuu:rounded-full niuu:px-2 niuu:text-text-secondary"
                                >
                                  {cat}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Per-mount recent activity excerpt */}
                      {mountFeed.length > 0 && (
                        <div>
                          <div className="niuu:text-xs niuu:text-text-muted niuu:mb-2">
                            recent activity
                          </div>
                          <div className="niuu:flex niuu:flex-col niuu:gap-1">
                            {mountFeed.map((entry) => (
                              <div
                                key={entry.id}
                                className="niuu:flex niuu:items-center niuu:gap-2 niuu:font-mono niuu:text-xs"
                              >
                                <span className="niuu:text-text-muted">
                                  {formatTimestamp(entry.timestamp)}
                                </span>
                                <span className="niuu:text-text-secondary">{entry.kind}</span>
                                <span className="niuu:text-text-secondary niuu:truncate">
                                  {entry.message}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>

        </div>

        {/* RIGHT column: activity feed */}
        <div className="niuu:p-5 niuu:overflow-y-auto">
          <div className="niuu:flex niuu:items-baseline niuu:gap-3 niuu:mb-4">
            <h3 className="niuu:m-0 niuu:text-base niuu:text-text-primary">Activity</h3>
            <span className="niuu:text-xs niuu:text-text-muted">all mounts · newest first</span>
          </div>
          {feed && feed.length > 0 ? (
            <div
              className="niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:overflow-hidden"
              aria-label="recent writes feed"
              role="log"
            >
              {feed.map((entry) => (
                <div
                  key={entry.id}
                  className="niuu:grid niuu:grid-cols-[58px_60px_66px_1fr] niuu:gap-3 niuu:items-center niuu:px-4 niuu:py-2 niuu:border-b niuu:border-border-subtle niuu:last:border-b-0 niuu:text-xs niuu:font-mono"
                >
                  <span className="niuu:text-text-muted">{formatTimestamp(entry.timestamp)}</span>
                  <span className={FEED_KIND_COLOR[entry.kind] ?? 'niuu:text-text-secondary'}>
                    {entry.kind}
                  </span>
                  <span>
                    <MountChip name={entry.mount} />
                  </span>
                  <span className="niuu:font-sans niuu:text-xs niuu:text-text-secondary niuu:truncate">
                    <span className="niuu:text-text-primary">{entry.ravn}</span>
                    <span className="niuu:text-text-muted">{' · '}</span>
                    {entry.message}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="niuu:text-sm niuu:text-text-muted niuu:m-0">No recent activity.</p>
          )}
        </div>
      </div>
    </div>
  );
}
