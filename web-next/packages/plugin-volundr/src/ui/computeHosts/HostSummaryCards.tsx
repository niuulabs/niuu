import type { ReactNode } from 'react';
import { Chip } from '@niuulabs/ui';
import {
  hostsByStateLine,
  warmRemedy,
  warmShortfall,
  type ComputeSummary,
} from '../../domain/computeHosts';

const PERCENT = 100;

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3.5 niuu:py-3">
      <div className="niuu:text-xs niuu:text-text-muted">{title}</div>
      {children}
    </div>
  );
}

function Row({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2 niuu:py-0.5 niuu:text-xs niuu:text-text-secondary">
      <span>{left}</span>
      <span>{right}</span>
    </div>
  );
}

function TagSet({ tags }: { tags: readonly string[] }) {
  if (!tags.length) return <span>all hosts</span>;
  return (
    <span className="niuu:inline-flex niuu:flex-wrap niuu:gap-1">
      {tags.map((tag) => (
        <Chip key={tag}>{tag}</Chip>
      ))}
    </span>
  );
}

/** Hosts by state, free slots, free slots per tag set and the warm targets. */
export function HostSummaryCards({ summary }: { summary: ComputeSummary }) {
  const used = summary.slotsTotal - summary.slotsFree;
  const usedPct = summary.slotsTotal > 0 ? (used / summary.slotsTotal) * PERCENT : 0;
  const stateLine = hostsByStateLine(summary.byState);
  const short = summary.warmTargets.filter((target) => !target.met);

  return (
    <div
      className="niuu:grid niuu:grid-cols-[repeat(auto-fit,minmax(12rem,1fr))] niuu:gap-3"
      data-testid="host-summary"
    >
      <Card title="Hosts">
        <div className="niuu:mt-0.5 niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
          {summary.hosts}
        </div>
        <div className="niuu:mt-0.5 niuu:text-xs niuu:text-text-secondary">
          {stateLine || 'No hosts yet'}
        </div>
      </Card>

      <Card title="Free slots">
        <div className="niuu:mt-0.5 niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
          {summary.slotsFree}{' '}
          <span className="niuu:text-xs niuu:font-normal niuu:text-text-muted">
            of {summary.slotsTotal}
          </span>
        </div>
        <div
          role="progressbar"
          aria-label="Slots in use"
          aria-valuemin={0}
          aria-valuemax={summary.slotsTotal}
          aria-valuenow={used}
          className="niuu:mt-2 niuu:h-1.5 niuu:overflow-hidden niuu:rounded-full niuu:bg-bg-tertiary"
        >
          <div className="niuu:h-full niuu:bg-brand" style={{ width: `${usedPct}%` }} />
        </div>
      </Card>

      <Card title="Free slots by tags">
        <div className="niuu:mt-1">
          {summary.freeByTagSet.length === 0 ? (
            <div className="niuu:text-xs niuu:text-text-muted">No hosts in service</div>
          ) : (
            summary.freeByTagSet.map((entry) => (
              <Row
                key={entry.tags.join('\n')}
                left={<TagSet tags={entry.tags} />}
                right={entry.free}
              />
            ))
          )}
        </div>
      </Card>

      <Card title="Warm targets">
        <div className="niuu:mt-1">
          {summary.warmTargets.length === 0 ? (
            <div className="niuu:text-xs niuu:text-text-muted">None set</div>
          ) : (
            summary.warmTargets.map((target) => (
              <Row
                key={`${target.match}:${target.tags.join('\n')}`}
                left={
                  <>
                    {target.tags.length ? <TagSet tags={target.tags} /> : 'pool'} · {target.min}{' '}
                    free
                  </>
                }
                right={
                  target.met ? (
                    <span className="niuu:inline-flex niuu:items-center niuu:gap-1.5">
                      {target.free}
                      <span className="niuu:rounded-full niuu:bg-state-ok-bg niuu:px-2 niuu:py-0.5 niuu:text-state-ok">
                        met
                      </span>
                    </span>
                  ) : (
                    <span className="niuu:text-critical-fg">
                      {target.free} · short by {warmShortfall(target)}
                    </span>
                  )
                }
              />
            ))
          )}
          {short.map((target) => (
            <div
              key={`remedy:${target.tags.join('\n')}`}
              className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary"
            >
              {warmRemedy(target)}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
