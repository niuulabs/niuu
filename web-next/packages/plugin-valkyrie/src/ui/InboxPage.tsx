import { useMemo, useState } from 'react';
import { Check, Gavel, GraduationCap, Hammer, Shield, TrendingUp, X } from 'lucide-react';
import {
  reviewArtifactEvidence,
  reviewEffectStatement,
  reviewPolicyFindings,
  type ReviewItem,
  type ReviewKind,
} from '../domain';
import { useDecideReview, useReviewList, useReviewSummary } from '../application/useReviews';
import { KIND_LABELS, riskClasses, sortByUrgency, statusClasses, timeAgo } from './reviewFormat';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const MUTED = 'niuu:text-text-muted';
const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:justify-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:hover:border-brand niuu:disabled:opacity-50';

const KIND_ICONS: Record<ReviewKind, typeof Hammer> = {
  evolution_build: Hammer,
  skill_promotion: TrendingUp,
  flock_learning: GraduationCap,
  court_escalation: Gavel,
  autonomy_change: Shield,
};

const ARTIFACT_TABS = ['skill', 'tool', 'canary', 'findings'] as const;
type ArtifactTab = (typeof ARTIFACT_TABS)[number];

function KindBadge({ kind }: { kind: ReviewKind }) {
  const Icon = KIND_ICONS[kind];
  return (
    <span className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-secondary">
      <Icon size={12} aria-hidden="true" />
      {KIND_LABELS[kind]}
    </span>
  );
}

function LineageStrip({ item }: { item: ReviewItem }) {
  const steps = useMemo(() => {
    const list: string[] = [];
    const signalIds = item.evidence.signal_ids;
    if (Array.isArray(signalIds) && signalIds.length > 0) {
      list.push(`signal ×${signalIds.length}`);
    }
    if (item.kind === 'evolution_build') list.push('micro-dream build');
    if (item.kind === 'flock_learning') list.push(`peer learning from ${item.environmentId}`);
    if (item.kind === 'skill_promotion') list.push('consolidation dream');
    if (item.kind === 'court_escalation') {
      const tier = item.evidence.tier;
      list.push(typeof tier === 'string' ? `court at ${tier}` : 'court');
    }
    if (item.kind === 'autonomy_change') list.push('operator command');
    const review = item.evidence.review;
    if (typeof review === 'object' && review !== null) {
      const outcome = (review as Record<string, unknown>).outcome;
      if (typeof outcome === 'string') list.push(`review: ${outcome.replace(/_/g, ' ')}`);
    }
    return list;
  }, [item]);
  return (
    <div data-testid="review-lineage" className={`niuu:flex niuu:flex-wrap niuu:gap-1 ${MUTED}`}>
      {steps.map((step, index) => (
        <span key={step} className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:text-xs">
          {index > 0 ? <span aria-hidden="true">→</span> : null}
          <span className="niuu:rounded-md niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5">{step}</span>
        </span>
      ))}
    </div>
  );
}

function ArtifactViewer({ item }: { item: ReviewItem }) {
  const [tab, setTab] = useState<ArtifactTab>('skill');
  const artifact = reviewArtifactEvidence(item);
  const findings = reviewPolicyFindings(item);
  const hasArtifact = Boolean(artifact.skillContent || artifact.toolCode);
  if (!hasArtifact && findings.length === 0) return null;

  const contentByTab: Record<ArtifactTab, string> = {
    skill: artifact.skillContent || 'No skill content attached.',
    tool: artifact.toolCode || 'No tool implementation attached.',
    canary:
      Object.keys(artifact.canarySample).length > 0
        ? JSON.stringify(artifact.canarySample, null, 2)
        : 'No canary sample attached.',
    findings: findings.length > 0 ? findings.join('\n') : 'No policy findings.',
  };

  return (
    <div data-testid="review-artifact">
      <div
        role="tablist"
        className="niuu:flex niuu:gap-1 niuu:border-b niuu:border-solid niuu:border-border"
      >
        {ARTIFACT_TABS.map((entry) => (
          <button
            key={entry}
            role="tab"
            type="button"
            aria-selected={tab === entry}
            onClick={() => setTab(entry)}
            className={`niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:capitalize ${
              tab === entry
                ? 'niuu:border-b-2 niuu:border-solid niuu:border-brand niuu:text-text-primary'
                : MUTED
            }`}
          >
            {entry}
          </button>
        ))}
      </div>
      <pre className="niuu:mt-2 niuu:max-h-72 niuu:overflow-auto niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:font-mono niuu:text-xs niuu:text-text-secondary niuu:whitespace-pre-wrap">
        {contentByTab[tab]}
      </pre>
    </div>
  );
}

function DecisionPanel({ item }: { item: ReviewItem }) {
  const decide = useDecideReview();
  const [reason, setReason] = useState('');
  const [validationError, setValidationError] = useState('');

  const submit = (decision: 'approved' | 'rejected') => {
    if (decision === 'rejected' && !reason.trim()) {
      setValidationError('A reason is required to reject.');
      return;
    }
    setValidationError('');
    decide.mutate({ itemId: item.itemId, decision, reason, participantId: 'human:operator' });
  };

  if (item.status !== 'pending') {
    return (
      <div data-testid="review-verdict" className={`${PANEL} niuu:p-3 niuu:text-sm`}>
        <span className={statusClasses(item.status)}>{item.status.replace('_', ' ')}</span>
        <span className={`niuu:ml-2 ${MUTED}`}>
          by {item.decidedBy || 'operator'}
          {item.decisionReason ? ` — ${item.decisionReason}` : ''}
        </span>
        {item.applyDetail ? (
          <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>{item.applyDetail}</p>
        ) : null}
      </div>
    );
  }

  return (
    <div data-testid="review-decision" className="niuu:flex niuu:flex-col niuu:gap-2">
      <p className={`niuu:rounded-md niuu:bg-bg-tertiary niuu:p-2 niuu:text-xs ${MUTED}`}>
        {reviewEffectStatement(item)}
      </p>
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <input
          aria-label="Decision reason"
          placeholder="Reason (recorded in the audit trail)"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="niuu:flex-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
        />
        <button
          type="button"
          className={`${BUTTON} niuu:text-state-ok`}
          disabled={decide.isPending}
          onClick={() => submit('approved')}
        >
          <Check size={14} aria-hidden="true" />
          Approve
        </button>
        <button
          type="button"
          className={`${BUTTON} niuu:text-critical`}
          disabled={decide.isPending}
          onClick={() => submit('rejected')}
        >
          <X size={14} aria-hidden="true" />
          Reject
        </button>
      </div>
      {validationError ? (
        <p role="alert" className="niuu:text-xs niuu:text-critical">
          {validationError}
        </p>
      ) : null}
      {decide.isError ? (
        <p role="alert" className="niuu:text-xs niuu:text-critical">
          {decide.error instanceof Error ? decide.error.message : 'Decision failed'}
        </p>
      ) : null}
    </div>
  );
}

function ReviewDetail({ item }: { item: ReviewItem }) {
  return (
    <section
      data-testid="review-detail"
      className={`${PANEL} niuu:flex niuu:min-h-0 niuu:flex-1 niuu:flex-col niuu:gap-3 niuu:overflow-auto niuu:p-4`}
    >
      <header className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-2">
        <div>
          <h2 className="niuu:text-base niuu:text-text-primary">{item.title}</h2>
          <p className={`niuu:text-xs ${MUTED}`}>
            {item.valkyrieId || item.environmentId} · {item.requestedAction} ·{' '}
            {item.safetyClass.replace('_', ' ')}
          </p>
        </div>
        <span className={`niuu:text-xs ${riskClasses(item.riskClass)}`}>{item.riskClass} risk</span>
      </header>
      {item.summary ? (
        <p className="niuu:text-sm niuu:text-text-secondary">{item.summary}</p>
      ) : null}
      <LineageStrip item={item} />
      <ArtifactViewer item={item} />
      <DecisionPanel item={item} />
    </section>
  );
}

function QueueCard({
  item,
  selected,
  onSelect,
}: {
  item: ReviewItem;
  selected: boolean;
  onSelect: (itemId: string) => void;
}) {
  return (
    <button
      type="button"
      data-testid="review-card"
      aria-pressed={selected}
      onClick={() => onSelect(item.itemId)}
      className={`${PANEL} niuu:w-full niuu:p-3 niuu:text-left ${
        selected ? 'niuu:border-brand' : 'niuu:hover:border-brand'
      }`}
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <KindBadge kind={item.kind} />
        <span className={`niuu:text-xs ${MUTED}`}>{timeAgo(item.requestedAt)}</span>
      </div>
      <p className="niuu:mt-2 niuu:truncate niuu:text-sm niuu:text-text-primary">{item.title}</p>
      <p className={`niuu:mt-1 niuu:truncate niuu:text-xs ${MUTED}`}>
        {item.environmentId}
        <span className={`niuu:ml-2 ${riskClasses(item.riskClass)}`}>{item.riskClass}</span>
      </p>
    </button>
  );
}

export function InboxPage() {
  const [kindFilter, setKindFilter] = useState<ReviewKind | ''>('');
  const [selectedId, setSelectedId] = useState<string>('');
  const { data: summary } = useReviewSummary();
  const { data, isLoading, error } = useReviewList({ status: 'pending', kind: kindFilter });

  const items = useMemo(() => sortByUrgency(data ?? []), [data]);
  const selected = items.find((item) => item.itemId === selectedId) ?? items[0];

  if (isLoading) {
    return (
      <div data-testid="inbox-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
        Loading review inbox…
      </div>
    );
  }
  if (error) {
    return (
      <div
        data-testid="inbox-error"
        role="alert"
        className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
      >
        {error instanceof Error ? error.message : 'Unable to load the review inbox'}
      </div>
    );
  }

  return (
    <div
      data-testid="inbox-page"
      className="niuu:flex niuu:h-full niuu:min-h-0 niuu:flex-col niuu:gap-3 niuu:bg-bg-primary niuu:p-3"
    >
      <header className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-3">
          <h1 className="niuu:text-base niuu:text-text-primary">Review inbox</h1>
          <span data-testid="inbox-pending-count" className={`niuu:text-xs ${MUTED}`}>
            {summary?.pendingTotal ?? items.length} pending
          </span>
          {summary &&
          (summary.pendingByRisk.high ?? 0) + (summary.pendingByRisk.critical ?? 0) > 0 ? (
            <span className="niuu:rounded-md niuu:bg-critical-bg niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-critical">
              {(summary.pendingByRisk.high ?? 0) + (summary.pendingByRisk.critical ?? 0)} high risk
            </span>
          ) : null}
        </div>
        <select
          aria-label="Filter by kind"
          value={kindFilter}
          onChange={(event) => setKindFilter(event.target.value as ReviewKind | '')}
          className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1.5 niuu:text-xs niuu:text-text-primary"
        >
          <option value="">all kinds</option>
          <option value="evolution_build">builds</option>
          <option value="skill_promotion">promotions</option>
          <option value="flock_learning">learnings</option>
          <option value="court_escalation">escalations</option>
          <option value="autonomy_change">autonomy</option>
        </select>
      </header>
      {items.length === 0 ? (
        <div data-testid="inbox-empty" className={`${PANEL} niuu:p-6 niuu:text-sm ${MUTED}`}>
          Nothing needs you. Held builds, guarded promotions, peer learnings, and court escalations
          will appear here the moment a valkyrie wants a decision.
        </div>
      ) : (
        <div className="niuu:flex niuu:min-h-0 niuu:flex-1 niuu:gap-3">
          <aside className="niuu:flex niuu:w-72 niuu:shrink-0 niuu:flex-col niuu:gap-2 niuu:overflow-auto">
            {items.map((item) => (
              <QueueCard
                key={item.itemId}
                item={item}
                selected={selected?.itemId === item.itemId}
                onSelect={setSelectedId}
              />
            ))}
          </aside>
          {selected ? <ReviewDetail item={selected} /> : null}
        </div>
      )}
    </div>
  );
}
