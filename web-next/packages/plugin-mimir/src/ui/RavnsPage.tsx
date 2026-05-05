import { useState } from 'react';
import { StateDot, Chip } from '@niuulabs/ui';
import { useRavns } from '../application/useRavns';
import type { RavnBinding } from '../domain/ravn-binding';
import { formatDuration, formatTimestamp } from './format';

// ---------------------------------------------------------------------------
// State pill helper
// ---------------------------------------------------------------------------

const STATE_PILL: Record<RavnBinding['state'], string> = {
  active: 'niuu-bg-bg-tertiary niuu-text-brand-200',
  idle: 'niuu-bg-bg-tertiary niuu-text-text-muted',
  offline: 'niuu-bg-critical-bg niuu-text-critical',
};

// ---------------------------------------------------------------------------
// Directory card
// ---------------------------------------------------------------------------

interface RavnCardProps {
  ravn: RavnBinding;
  onClick: () => void;
}

function RavnCard({ ravn, onClick }: RavnCardProps) {
  return (
    <article
      className="niuu-p-4 niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-gap-3 niuu-cursor-pointer niuu-transition-colors hover:niuu-border-border focus-visible:niuu-outline focus-visible:niuu-outline-2 focus-visible:niuu-outline-brand focus-visible:niuu-outline-offset-2"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick();
      }}
      data-testid="ravn-item"
      aria-label={`Warden ${ravn.ravnId}`}
    >
      {/* Head: avatar + name + state */}
      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <span
          className="niuu-inline-flex niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-font-bold niuu-text-text-secondary niuu-bg-bg-tertiary niuu-border niuu-border-border-subtle niuu-uppercase niuu-shrink-0"
          style={{ width: 36, height: 36, borderRadius: 'var(--radius-sm)' }}
          aria-hidden
        >
          {ravn.ravnId.charAt(0)}
          {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
        </span>
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-flex-1 niuu-min-w-0">
          <span className="niuu-font-mono niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-truncate">
            {ravn.ravnId}
          </span>
          <span
            className={`niuu-text-xs niuu-font-mono niuu-px-2 niuu-rounded-sm niuu-shrink-0 ${STATE_PILL[ravn.state]}`}
            data-testid="ravn-state"
          >
            {ravn.state}
          </span>
        </div>
      </div>

      {/* Role chip */}
      <div className="niuu-flex niuu-gap-1">
        <Chip tone="muted">{ravn.role}</Chip>
      </div>

      {/* Bio */}
      <p
        className="niuu-text-xs niuu-text-text-secondary niuu-m-0 niuu-line-clamp-2"
        data-testid="ravn-bio"
      >
        {ravn.bio}
      </p>

      {/* Mount chips */}
      <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
        {ravn.mountNames.map((m) => (
          <Chip key={m} tone={m === ravn.writeMount ? 'brand' : 'muted'}>
            {m === ravn.writeMount ? `✎ ${m}` : m}
          </Chip>
        ))}
      </div>

      {/* Metrics row: lifetime pages touched + last dream timestamp */}
      <div className="niuu-flex niuu-items-center niuu-gap-4 niuu-pt-2 niuu-border-t niuu-border-border-subtle niuu-text-xs niuu-font-mono">
        <span className="niuu-text-text-secondary">
          <strong className="niuu-text-text-primary">{ravn.pagesTouched}</strong> pages touched
        </span>
        {ravn.lastDream ? (
          <span className="niuu-text-text-muted" data-testid="ravn-dream">
            last dream {formatTimestamp(ravn.lastDream.timestamp)}
          </span>
        ) : (
          <span className="niuu-text-text-muted niuu-italic" data-testid="ravn-no-dream">
            no dream cycles yet
          </span>
        )}
      </div>

      {/* Dream cycle stats (single-cycle) */}
      {ravn.lastDream && (
        <div className="niuu-text-xs niuu-text-text-secondary">
          <strong className="niuu-text-text-primary">{ravn.lastDream.pagesUpdated}</strong> pages ·{' '}
          <strong className="niuu-text-text-primary">{ravn.lastDream.entitiesCreated}</strong>{' '}
          entities · {formatDuration(ravn.lastDream.durationMs)}
        </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Profile view
// ---------------------------------------------------------------------------

interface RavnProfileProps {
  ravn: RavnBinding;
  onBack: () => void;
}

function RavnProfile({ ravn, onBack }: RavnProfileProps) {
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-6" data-testid="ravn-profile">
      {/* Back navigation */}
      <button
        type="button"
        className="niuu-self-start niuu-bg-transparent niuu-border-none niuu-text-text-muted niuu-text-sm niuu-cursor-pointer niuu-p-0 hover:niuu-text-text-secondary"
        onClick={onBack}
        aria-label="Back to wardens list"
      >
        ← Wardens
      </button>

      {/* Hero section */}
      <div className="niuu-flex niuu-items-center niuu-gap-4 niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg">
        <span
          className="niuu-inline-flex niuu-items-center niuu-justify-center niuu-font-mono niuu-text-xl niuu-font-bold niuu-text-text-secondary niuu-bg-bg-tertiary niuu-border niuu-border-border-subtle niuu-uppercase niuu-shrink-0"
          style={{ width: 48, height: 48, borderRadius: 'var(--radius-sm)' }}
          aria-hidden
        >
          {ravn.ravnId.charAt(0)}
          {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
        </span>
        <div className="niuu-flex niuu-flex-col niuu-gap-2">
          <h2 className="niuu-m-0 niuu-text-xl niuu-font-mono">{ravn.ravnId}</h2>
          <div className="niuu-flex niuu-items-center niuu-gap-2">
            <Chip tone="muted">{ravn.role}</Chip>
            <span
              className={`niuu-text-xs niuu-font-mono niuu-px-2 niuu-rounded-sm ${STATE_PILL[ravn.state]}`}
              data-testid="ravn-state"
            >
              {ravn.state}
            </span>
          </div>
          {/* Tools row */}
          {ravn.tools.length > 0 && (
            <span
              className="niuu-text-xs niuu-font-mono niuu-text-text-muted"
              data-testid="ravn-tools"
            >
              tools: {ravn.tools.join(' · ')}
            </span>
          )}
        </div>
      </div>

      {/* Grid panels */}
      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-4">
        {/* Mount bindings */}
        <section className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg">
          <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
            Mount bindings
          </h4>
          <div className="niuu-flex niuu-flex-col niuu-gap-2">
            {ravn.mountNames.map((m) => (
              <div key={m} className="niuu-flex niuu-items-center niuu-gap-2">
                <Chip tone={m === ravn.writeMount ? 'brand' : 'muted'}>
                  {m === ravn.writeMount ? `✎ ${m}` : m}
                </Chip>
                {m === ravn.writeMount && (
                  <span className="niuu-text-xs niuu-text-text-muted">write mount</span>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Areas of expertise */}
        <section
          className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
          data-testid="ravn-expertise"
        >
          <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
            Areas of expertise
          </h4>
          {ravn.expertise.length > 0 ? (
            <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
              {ravn.expertise.map((e) => (
                <Chip key={e} tone="brand">
                  {e}
                </Chip>
              ))}
            </div>
          ) : (
            <p className="niuu-text-sm niuu-text-text-muted niuu-italic niuu-m-0">
              no expertise defined
            </p>
          )}
        </section>

        {/* Dream stats */}
        {ravn.lastDream ? (
          <section
            className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
            data-testid="ravn-dream"
          >
            <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
              Last dream
            </h4>
            <div className="niuu-flex niuu-flex-col niuu-gap-2">
              {(
                [
                  ['time', formatTimestamp(ravn.lastDream.timestamp), false],
                  ['pages updated', String(ravn.lastDream.pagesUpdated), true],
                  ['entities created', String(ravn.lastDream.entitiesCreated), true],
                  ['lint fixes', String(ravn.lastDream.lintFixes), true],
                  ['duration', formatDuration(ravn.lastDream.durationMs), false],
                ] as [string, string, boolean][]
              ).map(([label, value, bold]) => (
                <div
                  key={label}
                  className="niuu-flex niuu-justify-between niuu-items-baseline niuu-py-[3px] niuu-border-b niuu-border-border-subtle niuu-text-xs last:niuu-border-b-0"
                >
                  <span className="niuu-text-text-muted">{label}</span>
                  {bold ? (
                    <strong className="niuu-text-text-primary">{value}</strong>
                  ) : (
                    <span className="niuu-font-mono niuu-text-text-secondary">{value}</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : (
          <section
            className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
            data-testid="ravn-no-dream"
          >
            <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
              Last dream
            </h4>
            <p className="niuu-text-sm niuu-text-text-muted niuu-italic niuu-m-0">
              no dream cycles yet
            </p>
          </section>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function RavnsPage() {
  const { data, isLoading, isError, error } = useRavns();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected = selectedId ? (data ?? []).find((r) => r.ravnId === selectedId) : null;

  if (selected) {
    return (
      <div className="niuu-p-6">
        <RavnProfile ravn={selected} onBack={() => setSelectedId(null)} />
      </div>
    );
  }

  return (
    <div className="niuu-p-6">
      <h2 className="niuu-m-0 niuu-mb-2 niuu-text-2xl niuu-font-semibold niuu-text-text-primary">
        Ravns
      </h2>
      <p className="niuu-m-0 niuu-mb-6 niuu-text-sm niuu-text-text-secondary">
        Per-ravn mount bindings and last dream-cycle summary.
      </p>

      {isLoading && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
          <StateDot state="processing" pulse />
          <span>loading ravns…</span>
        </div>
      )}

      {isError && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
          <StateDot state="failed" />
          <span>{error instanceof Error ? error.message : 'ravns load failed'}</span>
        </div>
      )}

      {!isLoading && !isError && data?.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted">No ravn bindings found.</p>
      )}

      {data && data.length > 0 && (
        <div className="niuu-grid niuu-grid-cols-[repeat(auto-fill,minmax(280px,1fr))] niuu-gap-4">
          {data.map((ravn) => (
            <RavnCard key={ravn.ravnId} ravn={ravn} onClick={() => setSelectedId(ravn.ravnId)} />
          ))}
        </div>
      )}
    </div>
  );
}
