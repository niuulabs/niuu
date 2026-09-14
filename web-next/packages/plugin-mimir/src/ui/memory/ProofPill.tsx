/**
 * ProofPill — how well-proven one written fact is.
 *
 * The pill says only what the backend counted: how many proofs bear on the
 * fact and which way the trend is going. Nothing is inferred or softened.
 */

import { Clock, Minus, Sparkles, TrendingDown, TrendingUp } from 'lucide-react';
import type { EvidenceTrend, FactEvidence } from '../../domain/evidence';

interface TrendStyle {
  icon: typeof Clock;
  className: string;
  /** What the trend means, shown on hover. */
  title: string;
}

const TREND_STYLE: Record<EvidenceTrend, TrendStyle> = {
  strengthening: {
    icon: TrendingUp,
    className: 'niuu:border-state-ok/50 niuu:text-state-ok',
    title: 'fresh support, and more of it than before',
  },
  stable: {
    icon: Minus,
    className: 'niuu:border-border-subtle niuu:text-text-secondary',
    title: 'recent support, holding steady',
  },
  weakening: {
    icon: TrendingDown,
    className: 'niuu:border-status-amber/50 niuu:text-status-amber',
    title: 'the newest proof is getting old',
  },
  stale: {
    icon: Clock,
    className: 'niuu:border-critical-bo niuu:text-critical-fg',
    title: 'the newest proof is old enough to distrust',
  },
  new: {
    icon: Sparkles,
    className: 'niuu:border-brand/50 niuu:text-brand-300',
    title: 'written, nothing supports it yet',
  },
};

export interface ProofPillProps {
  evidence: FactEvidence;
}

export function ProofPill({ evidence }: ProofPillProps) {
  const style = TREND_STYLE[evidence.trend];
  const Icon = style.icon;
  const proofs = `${evidence.proofCount} ${evidence.proofCount === 1 ? 'proof' : 'proofs'}`;
  const support = evidence.latestSupport
    ? `newest proof ${evidence.latestSupport}`
    : 'no proof on record';

  return (
    <span
      className={`niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-full niuu:border niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:whitespace-nowrap ${style.className}`}
      title={`${style.title} · ${support}`}
      data-testid="proof-pill"
      data-trend={evidence.trend}
    >
      <Icon size={11} aria-hidden="true" />
      {proofs} · {evidence.trend}
    </span>
  );
}
