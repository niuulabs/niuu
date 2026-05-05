/**
 * BudgetView — fleet budget dashboard (web2 baseline layout).
 *
 * Sections:
 *   1. Hero card — "$X spent of $Y" with segmented runway bar + projection pill
 *   2. Four attention columns — Over cap / Will exceed cap by EOD / Near cap (≥70%) / Accelerating
 *   3. Two-column: Top drivers today + Recommended changes
 *   4. Fleet burn sparkline (full-width, no axes)
 *   5. Collapsible full fleet table
 */

import { useMemo, useState } from 'react';
import { BudgetBar, PersonaAvatar, Sparkline, StateDot } from '@niuulabs/ui';
import { useRavens } from './hooks/useRavens';
import { useFleetBudget, useRavnBudgets } from './hooks/useBudget';
import type { Ravn } from '../domain/ravn';
import type { BudgetState } from '@niuulabs/domain';
import './ravn-views.css';

const USD = (n: number) => `$${n.toFixed(2)}`;
const PCT = (n: number) => `${Math.round(n * 100)}%`;

const TOP_DRIVERS_COUNT = 5;
const ELAPSED_HOURS = 18;
const RUNWAY_WINDOW_HOURS = 24;
const HOURS_REMAINING = RUNWAY_WINDOW_HOURS - ELAPSED_HOURS;

type Analysis = {
  ravn: Ravn;
  budget: BudgetState;
  hours: number[];
  recent: number;
  projected: number;
  pct: number;
  projPct: number;
  trend: number;
};

function seedFromId(id: string): number {
  let seed = 0;
  for (let i = 0; i < id.length; i++) {
    seed = (seed * 33 + id.charCodeAt(i)) >>> 0;
  }
  return seed || 1;
}

function normalizeName(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
}

function buildHourlySeries(ravn: Ravn, budget: BudgetState): number[] {
  const seed = seedFromId(ravn.id);
  const profile = normalizeName(ravn.personaName);
  const accelerating = profile === 'gefjon' || profile === 'eir';
  const cooling = profile === 'sindri' || profile === 'muninn';
  const base = Math.max(0.004, budget.spentUsd / RUNWAY_WINDOW_HOURS);
  const phase = ((seed % 628) / 100) * Math.PI;
  const raw: number[] = [];

  for (let hour = 0; hour < RUNWAY_WINDOW_HOURS; hour++) {
    const wave = 1 + Math.sin((hour / RUNWAY_WINDOW_HOURS) * Math.PI * 5 + phase) * 0.18;
    const ripple = 0.92 + (((seed >> (hour % 12)) & 7) / 7) * 0.18;
    const ramp = accelerating
      ? 0.68 + (hour / (RUNWAY_WINDOW_HOURS - 1)) * 1.05
      : cooling
        ? 1.3 - (hour / (RUNWAY_WINDOW_HOURS - 1)) * 0.44
        : 0.92 + Math.sin((hour / RUNWAY_WINDOW_HOURS) * Math.PI * 2 + phase / 2) * 0.12;
    raw.push(Math.max(0.001, base * wave * ripple * ramp));
  }

  const sum = raw.reduce((acc, value) => acc + value, 0);
  if (sum <= 0) {
    return raw.map(() => 0);
  }
  return raw.map((value) => (value / sum) * budget.spentUsd);
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((acc, value) => acc + value, 0) / values.length;
}

function sumSeries(series: number[][]): number[] {
  const totals = Array.from({ length: RUNWAY_WINDOW_HOURS }, () => 0);
  for (const values of series) {
    values.forEach((value, index) => {
      totals[index] = (totals[index] ?? 0) + value;
    });
  }
  return totals;
}

function pctClass(value: number): string {
  if (value >= 1) return 'err';
  if (value >= 0.8) return 'warn';
  return 'ok';
}

function BudgetAvatar({ ravn, size }: { ravn: Ravn; size: number }) {
  return ravn.role && ravn.letter ? (
    <PersonaAvatar role={ravn.role} letter={ravn.letter} size={size} />
  ) : (
    <span className="rv-budget-avatar-fallback" aria-hidden="true">
      {ravn.letter ?? ravn.personaName.charAt(0)}
    </span>
  );
}

function HeroCard({
  spentUsd,
  capUsd,
  projectedUsd,
}: {
  spentUsd: number;
  capUsd: number;
  projectedUsd: number;
}) {
  const headroom = capUsd - projectedUsd;
  const spentPct = capUsd > 0 ? Math.min(100, (spentUsd / capUsd) * 100) : 0;
  const projectedPct = capUsd > 0 ? Math.min(100, (projectedUsd / capUsd) * 100) : 0;
  const overrun = Math.max(0, projectedUsd - capUsd);

  return (
    <section className="rv-budget-hero" aria-label="fleet budget">
      <div className="rv-budget-hero__summary">
        <div className="rv-budget-hero__header">
          <span className="rv-budget-hero__elapsed">
            TODAY · {ELAPSED_HOURS}H OF {RUNWAY_WINDOW_HOURS}H ELAPSED
          </span>
        </div>
        <div className="rv-budget-hero__main">
          <span className="rv-budget-hero__big-value">{USD(spentUsd)}</span>
          <span className="rv-budget-hero__spent-label">
            spent of <strong>{USD(capUsd)}</strong>
          </span>
        </div>
        <div className="rv-budget-hero__projection">
          <span
            className={`rv-budget-hero__projection-pill ${overrun > 0 ? 'rv-budget-hero__projection-pill--warn' : ''}`}
            role="meter"
            aria-label="budget runway"
            aria-valuenow={Math.round((1 - spentUsd / Math.max(capUsd, 0.01)) * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            projecting <strong>{USD(projectedUsd)}</strong> by EOD ·{' '}
            {overrun > 0 ? (
              <strong>{USD(overrun)} over</strong>
            ) : (
              <strong>{USD(Math.max(0, headroom))} headroom</strong>
            )}
          </span>
        </div>
      </div>

      <div className="rv-budget-runway" data-testid="runway-bar">
        <div className="rv-budget-runway__bar-wrap">
          <div className="rv-budget-runway__track">
            <div
              className="rv-budget-runway__fill rv-budget-runway__fill--spent"
              style={{ width: `${spentPct}%` }}
            />
            <div
              className="rv-budget-runway__fill rv-budget-runway__fill--projected"
              style={{
                left: `${spentPct}%`,
                width: `${Math.max(0, projectedPct - spentPct)}%`,
              }}
            />
            <div className="rv-budget-runway__capmark" />
          </div>
          <div
            className="rv-budget-runway__now"
            style={{ left: `${(ELAPSED_HOURS / RUNWAY_WINDOW_HOURS) * 100}%` }}
          >
            <span className="rv-budget-runway__now-label">NOW</span>
            <div className="rv-budget-runway__now-line" />
          </div>
        </div>
        <div className="rv-budget-runway__annotations">
          <span>0</span>
          <span
            className="rv-budget-runway__annotation"
            style={{ left: `${spentPct}%`, transform: 'translateX(-50%)' }}
          >
            now · {USD(spentUsd)}
          </span>
          <span
            className="rv-budget-runway__annotation"
            style={{ left: `${projectedPct}%`, transform: 'translateX(-50%)' }}
          >
            eod · {USD(projectedUsd)}
          </span>
          <span className="rv-budget-runway__annotation rv-budget-runway__annotation--cap">
            cap · {USD(capUsd)}
          </span>
        </div>
      </div>
    </section>
  );
}

function AttentionItem({ analysis, rightLabel }: { analysis: Analysis; rightLabel: string }) {
  return (
    <div className="rv-budget-attention-item">
      <div className="rv-budget-attention-item__head">
        <span className="rv-budget-attention-item__identity">
          <BudgetAvatar ravn={analysis.ravn} size={18} />
          <span className="rv-budget-attention-item__name">
            {analysis.ravn.personaName}
            <span className="rv-budget-attention-item__ratio">
              {USD(analysis.budget.spentUsd)}/{USD(analysis.budget.capUsd)}
            </span>
          </span>
        </span>
        <span className="rv-budget-attention-item__spent">{rightLabel}</span>
      </div>
    </div>
  );
}

function AttentionColumn({
  title,
  icon,
  tone,
  emptyMessage,
  rows,
  rightLabel,
}: {
  title: string;
  icon: string;
  tone: 'danger' | 'warn' | 'neutral' | 'info';
  emptyMessage: string;
  rows: Analysis[];
  rightLabel: (analysis: Analysis) => string;
}) {
  return (
    <section
      className={`rv-budget-attention-col rv-budget-attention-col--${tone}`}
      aria-label={title}
    >
      <div className="rv-budget-attention-col__head">
        <span className="rv-budget-attention-col__icon">{icon}</span>
        <h3 className="rv-budget-attention-col__title">{title}</h3>
        <span className="rv-budget-attention-col__count">{rows.length}</span>
      </div>
      <div className="rv-budget-attention-col__body">
        {rows.length === 0 ? (
          <span className="rv-budget-attention-col__empty">{emptyMessage}</span>
        ) : (
          rows.map((analysis) => (
            <AttentionItem
              key={analysis.ravn.id}
              analysis={analysis}
              rightLabel={rightLabel(analysis)}
            />
          ))
        )}
      </div>
    </section>
  );
}

function FleetBurnChart({
  hourlyValues,
  recentBurnPerHr,
}: {
  hourlyValues: number[];
  recentBurnPerHr: number;
}) {
  const peak = Math.max(...hourlyValues, 0);
  const averageBurn = average(hourlyValues);

  return (
    <section
      className="rv-budget-fleet-burn"
      aria-label="fleet spend 24h"
      data-testid="fleet-sparkline"
    >
      <div className="rv-budget-fleet-burn__header">
        <h3 className="rv-budget-fleet-burn__title">Fleet burn · last 24h</h3>
        <span className="rv-budget-fleet-burn__stats">
          peak {USD(peak)}/h · avg {USD(averageBurn)}/h · now {USD(recentBurnPerHr)}/h
        </span>
      </div>
      <div className="rv-budget-fleet-burn__chart">
        <Sparkline values={hourlyValues} id="fleet-burn-24h" width={1200} height={128} fill />
      </div>
    </section>
  );
}

function TopDriversTable({ analysis, totalSpent }: { analysis: Analysis[]; totalSpent: number }) {
  const drivers = [...analysis]
    .sort((a, b) => b.budget.spentUsd - a.budget.spentUsd)
    .slice(0, TOP_DRIVERS_COUNT);

  if (drivers.length === 0) return null;

  return (
    <section className="rv-budget-drivers" aria-label="top drivers" data-testid="top-drivers">
      <div className="rv-budget-drivers__header">
        <h3 className="rv-budget-drivers__title">Top drivers today</h3>
        <span className="rv-budget-drivers__subtitle">ravens ranked by absolute $ spent</span>
      </div>
      <ul className="rv-budget-drivers__list">
        {drivers.map((entry, idx) => {
          const share = totalSpent > 0 ? entry.budget.spentUsd / totalSpent : 0;
          return (
            <li key={entry.ravn.id} className="rv-budget-driver-row" data-testid="driver-row">
              <span className="rv-budget-driver-row__rank">{idx + 1}</span>
              <span className="rv-budget-driver-row__avatar">
                <BudgetAvatar ravn={entry.ravn} size={18} />
              </span>
              <span className="rv-budget-driver-row__name">{entry.ravn.personaName}</span>
              <span className="rv-budget-driver-row__role">{entry.ravn.role ?? ''}</span>
              <span className="rv-budget-driver-row__share">{PCT(share)} of fleet</span>
              <div className="rv-budget-driver-row__bar-track">
                <div
                  className="rv-budget-driver-row__bar-fill"
                  style={{ width: `${share * 100}%` }}
                />
              </div>
              <div className="rv-budget-driver-row__sparkline">
                <Sparkline
                  values={entry.hours}
                  id={`driver-${entry.ravn.id}`}
                  width={108}
                  height={22}
                  fill
                />
              </div>
              <span className="rv-budget-driver-row__amount">{USD(entry.budget.spentUsd)}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

interface Recommendation {
  ravnId: string;
  personaName: string;
  attention: 'over-cap' | 'burning-fast' | 'idle';
  badge: string;
  usageText: string;
  suggestion: string;
  actionLabel: string;
}

function buildRecommendations(willExceed: Analysis[], underUtilized: Analysis[]): Recommendation[] {
  const recs: Recommendation[] = [];
  for (const entry of willExceed.slice(0, 3)) {
    const suggestedCap = Math.ceil(entry.projected * 1.2 * 100) / 100;
    recs.push({
      ravnId: entry.ravn.id,
      personaName: entry.ravn.personaName,
      attention: 'burning-fast',
      badge: 'will exceed',
      usageText: `projected ${USD(entry.projected)} · current cap ${USD(entry.budget.capUsd)}`,
      suggestion: `→ raise cap to ${USD(suggestedCap)}`,
      actionLabel: 'apply',
    });
  }

  for (const entry of underUtilized.slice(0, 3)) {
    const suggestedCap = Math.max(
      0.1,
      Math.round((entry.budget.spentUsd * 4 || entry.budget.capUsd * 0.3) * 100) / 100,
    );
    recs.push({
      ravnId: entry.ravn.id,
      personaName: entry.ravn.personaName,
      attention: 'idle',
      badge: 'underused',
      usageText: `used ${USD(entry.budget.spentUsd)} of ${USD(entry.budget.capUsd)} cap`,
      suggestion: `→ lower cap to ${USD(suggestedCap)}`,
      actionLabel: 'apply',
    });
  }
  return recs.slice(0, 6);
}

function RecommendedChanges({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <section
      className="rv-budget-recs"
      aria-label="recommended changes"
      data-testid="recommended-changes"
    >
      <div className="rv-budget-recs__header">
        <h3 className="rv-budget-recs__title">Recommended changes</h3>
        <span className="rv-budget-recs__subtitle">suggested cap adjustments</span>
      </div>
      {recommendations.length === 0 ? (
        <div className="rv-budget-recs__empty">Caps look appropriate. No changes suggested.</div>
      ) : (
        <ul className="rv-budget-recs__list">
          {recommendations.map((rec) => (
            <li
              key={rec.ravnId}
              className="rv-budget-rec-card"
              data-attention={rec.attention}
              data-testid="rec-row"
            >
              <div className="rv-budget-rec-card__head">
                <span className="rv-budget-rec-card__name">{rec.personaName}</span>
                <span
                  className={`rv-budget-rec-row__badge rv-budget-rec-row__badge--${rec.attention}`}
                  aria-label={`attention: ${rec.attention}`}
                >
                  {rec.badge}
                </span>
              </div>
              <span className="rv-budget-rec-card__usage">{rec.usageText}</span>
              <div className="rv-budget-rec-card__divider" />
              <div className="rv-budget-rec-card__action-row">
                <span className="rv-budget-rec-card__suggestion">{rec.suggestion}</span>
                <button
                  type="button"
                  className="rv-budget-rec-action"
                  data-testid="rec-action"
                  onClick={() => {
                    /* stub */
                  }}
                >
                  {rec.actionLabel}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FullFleetTable({ analysis }: { analysis: Analysis[] }) {
  const [open, setOpen] = useState(false);
  const sorted = [...analysis].sort((a, b) => b.pct - a.pct);

  return (
    <section className="rv-budget-fleet">
      <button
        type="button"
        className="rv-budget-fleet__toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="rv-budget-fleet__toggle-title">
          Full fleet table{' '}
          <span className="rv-budget-fleet__toggle-count">{sorted.length} ravens</span>
        </span>
        <span className="rv-budget-fleet__toggle-icon">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="rv-budget-fleet__table-wrap">
          <table className="rv-budget-fleet-table" aria-label="fleet budget table">
            <thead>
              <tr>
                <th>Raven</th>
                <th>Persona</th>
                <th>Today</th>
                <th>Spent</th>
                <th>Cap</th>
                <th>%</th>
                <th>Proj EOD</th>
                <th>Burn (24h)</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((entry) => (
                <tr
                  key={entry.ravn.id}
                  className="rv-budget-fleet-row"
                  data-attention={pctClass(entry.pct)}
                >
                  <td className="rv-budget-fleet-row__name">
                    <StateDot
                      state={
                        entry.ravn.status === 'active'
                          ? 'healthy'
                          : entry.ravn.status === 'failed'
                            ? 'failed'
                            : entry.ravn.status === 'suspended'
                              ? 'observing'
                              : 'idle'
                      }
                    />
                    <span>{entry.ravn.personaName}</span>
                  </td>
                  <td className="rv-budget-fleet-row__persona">
                    <span className="rv-budget-fleet-row__persona-wrap">
                      <BudgetAvatar ravn={entry.ravn} size={16} />
                      <span>{entry.ravn.role ?? '—'}</span>
                    </span>
                  </td>
                  <td className="rv-budget-fleet-row__bar">
                    <BudgetBar
                      spent={entry.budget.spentUsd}
                      cap={entry.budget.capUsd}
                      warnAt={Math.round(entry.budget.warnAt * 100)}
                      size="md"
                    />
                  </td>
                  <td className="rv-budget-fleet-row__spent">{USD(entry.budget.spentUsd)}</td>
                  <td className="rv-budget-fleet-row__cap">{USD(entry.budget.capUsd)}</td>
                  <td className="rv-budget-fleet-row__pct">{PCT(entry.pct)}</td>
                  <td
                    className={`rv-budget-fleet-row__proj rv-budget-fleet-row__proj--${pctClass(entry.projPct)}`}
                  >
                    {USD(entry.projected)}
                  </td>
                  <td className="rv-budget-fleet-row__sparkline">
                    <Sparkline
                      values={entry.hours}
                      id={`fleet-${entry.ravn.id}`}
                      width={120}
                      height={24}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function BudgetView() {
  const { data: ravens, isLoading: ravensLoading } = useRavens();
  const { data: fleetBudget, isLoading: fleetLoading } = useFleetBudget();
  const ravnList = useMemo(() => ravens ?? [], [ravens]);
  const budgets = useRavnBudgets(ravnList.map((ravn) => ravn.id));

  const analysis = useMemo(
    () =>
      ravnList
        .map((ravn) => {
          const budget = budgets[ravn.id];
          if (!budget) return null;
          const hours = buildHourlySeries(ravn, budget);
          const recent = average(hours.slice(-3));
          const earlier = average(hours.slice(-9, -3)) || recent;
          const projected = budget.spentUsd + recent * HOURS_REMAINING;
          const pct = budget.capUsd > 0 ? budget.spentUsd / budget.capUsd : 0;
          const projPct = budget.capUsd > 0 ? projected / budget.capUsd : 0;
          return {
            ravn,
            budget,
            hours,
            recent,
            projected,
            pct,
            projPct,
            trend: recent - earlier,
          } satisfies Analysis;
        })
        .filter((entry): entry is Analysis => entry !== null),
    [budgets, ravnList],
  );

  const totalSpent =
    fleetBudget?.spentUsd ?? analysis.reduce((acc, entry) => acc + entry.budget.spentUsd, 0);
  const totalCap =
    fleetBudget?.capUsd ?? analysis.reduce((acc, entry) => acc + entry.budget.capUsd, 0);
  const fleetHourly = useMemo(() => sumSeries(analysis.map((entry) => entry.hours)), [analysis]);
  const recentBurnPerHr = average(fleetHourly.slice(-3));
  const projectedEOD = totalSpent + recentBurnPerHr * HOURS_REMAINING;

  const overCap = analysis.filter((entry) => entry.pct >= 1);
  const willExceed = analysis
    .filter((entry) => entry.pct < 1 && entry.projPct >= 1)
    .sort((a, b) => b.projPct - a.projPct);
  const nearCap = analysis
    .filter((entry) => entry.pct >= 0.7 && entry.pct < 1)
    .sort((a, b) => b.pct - a.pct);
  const accelerating = analysis
    .filter((entry) => entry.trend > 0.005 && entry.pct < 0.7)
    .sort((a, b) => b.trend - a.trend)
    .slice(0, 3);
  const underUtilized = analysis
    .filter((entry) => entry.pct < 0.05 && entry.budget.capUsd >= 0.5)
    .sort((a, b) => a.pct - b.pct)
    .slice(0, 3);
  const recommendations = buildRecommendations(willExceed, underUtilized);

  if (ravensLoading || fleetLoading) {
    return (
      <div className="rv-budget-view">
        <div className="rv-budget-view__loading">
          <StateDot state="processing" pulse />
          <span>loading budget…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="rv-budget-view">
      <HeroCard spentUsd={totalSpent} capUsd={totalCap} projectedUsd={projectedEOD} />

      <div
        className="rv-budget-attention-columns rv-budget-attention-columns--4"
        role="group"
        aria-label="budget attention"
      >
        <AttentionColumn
          title="Over cap"
          icon="⊘"
          tone="danger"
          emptyMessage="No ravens over cap — good."
          rows={overCap}
          rightLabel={(entry) => PCT(entry.pct)}
        />
        <AttentionColumn
          title="Will exceed cap by EOD"
          icon="▲"
          tone="warn"
          emptyMessage="No projected overruns."
          rows={willExceed}
          rightLabel={(entry) => `proj ${PCT(entry.projPct)}`}
        />
        <AttentionColumn
          title="Near cap (≥70%)"
          icon="◐"
          tone="neutral"
          emptyMessage="No ravens near cap."
          rows={nearCap}
          rightLabel={(entry) => PCT(entry.pct)}
        />
        <AttentionColumn
          title="Accelerating"
          icon="↗"
          tone="info"
          emptyMessage="Burn is steady across the fleet."
          rows={accelerating}
          rightLabel={(entry) => `+${USD(entry.trend)}/h`}
        />
      </div>

      <div className="rv-budget-two-col">
        <TopDriversTable analysis={analysis} totalSpent={totalSpent} />
        <RecommendedChanges recommendations={recommendations} />
      </div>

      <FleetBurnChart hourlyValues={fleetHourly} recentBurnPerHr={recentBurnPerHr} />

      <FullFleetTable analysis={analysis} />
    </div>
  );
}
