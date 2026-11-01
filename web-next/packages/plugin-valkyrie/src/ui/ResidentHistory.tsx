import { useMemo, useState } from 'react';
import { AlertTriangle, ChevronDown, MessageSquare } from 'lucide-react';
import {
  collapseDecisionsByCorrelation,
  decisionHeadline,
  decisionNeedsApproval,
  decisionSubject,
  referencedSkillName,
  LIST_LIMIT,
  type DecisionRecord,
  type GroupedDecision,
  type LearnedSkillSummary,
  type OperationalState,
} from '../domain';
import {
  useDecisionDetail,
  useDecisionList,
  useSignalHistory,
} from '../application/useValkyrieHistory';
import { useValkyrieSkills } from '../application/useValkyrieSkills';
import { EvolutionFold } from './EvolutionPanel';
import { SkillNameButton } from './SkillViewer';
import { timeAgo } from './reviewFormat';
import { decisionStatusCopy, operationalStateCopy, outcomeCopy, severityCopy } from './copy';

const PANEL_PAD =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-4';
const MUTED = 'niuu:text-text-muted';
const SIGNAL_PAGE_SIZE = 10;

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function HistoryUnavailable({ what }: { what: string }) {
  return (
    <p
      data-testid="valkyrie-history-unavailable"
      className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:text-xs niuu:text-critical"
    >
      {what} could not be loaded — this API backend does not serve decision history yet. Redeploy
      the platform API with the current build to enable it.
    </p>
  );
}

function SignalBrowser({ environmentId }: { environmentId: string }) {
  const [severity, setSeverity] = useState('');
  const [offset, setOffset] = useState(0);
  const { data, isLoading, error } = useSignalHistory({
    environmentId,
    severity: severity || undefined,
    limit: SIGNAL_PAGE_SIZE,
    offset,
  });

  return (
    <div
      data-testid="valkyrie-signal-browser"
      className="niuu:mt-4 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
        <span className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
          observed signals {data ? `(${data.total})` : ''}
        </span>
        <select
          aria-label="Filter signals by severity"
          value={severity}
          onChange={(event) => {
            setSeverity(event.target.value);
            setOffset(0);
          }}
          className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-primary"
        >
          <option value="">all severities</option>
          {['info', 'notice', 'warning', 'critical'].map((entry) => (
            <option key={entry} value={entry}>
              {severityCopy(entry).label}
            </option>
          ))}
        </select>
      </div>
      <div className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-2">
        {isLoading ? <p className={`niuu:text-xs ${MUTED}`}>Loading signals...</p> : null}
        {error ? <HistoryUnavailable what="Observed signals" /> : null}
        {(data?.items ?? []).map((signal) => (
          <div key={signal.signalId} className="niuu:rounded-md niuu:bg-bg-secondary niuu:p-2">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
              <span className="niuu:truncate niuu:font-mono niuu:text-xs niuu:text-brand">
                {signal.subject}
              </span>
              <span className="niuu:shrink-0 niuu:text-[10px] niuu:text-text-muted">
                {severityCopy(signal.severity).label} · {timeAgo(signal.receivedAt)} ago
              </span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary">{signal.summary}</p>
          </div>
        ))}
        {data && data.items.length === 0 ? (
          <p className={`niuu:text-xs ${MUTED}`}>No stored signals match this filter.</p>
        ) : null}
      </div>
      {data && data.total > SIGNAL_PAGE_SIZE ? (
        <div className="niuu:mt-3 niuu:flex niuu:items-center niuu:justify-between niuu:text-xs">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - SIGNAL_PAGE_SIZE))}
            className="niuu:rounded-md niuu:border niuu:border-border niuu:px-2 niuu:py-1 niuu:text-text-secondary niuu:disabled:opacity-40"
          >
            newer
          </button>
          <span className={MUTED}>
            {offset + 1}–{Math.min(offset + SIGNAL_PAGE_SIZE, data.total)} of {data.total}
          </span>
          <button
            type="button"
            disabled={offset + SIGNAL_PAGE_SIZE >= data.total}
            onClick={() => setOffset(offset + SIGNAL_PAGE_SIZE)}
            className="niuu:rounded-md niuu:border niuu:border-border niuu:px-2 niuu:py-1 niuu:text-text-secondary niuu:disabled:opacity-40"
          >
            older
          </button>
        </div>
      ) : null}
    </div>
  );
}

function DecisionCard({
  decision,
  count,
  expanded,
  onToggle,
  skillName,
  onViewSkill,
}: {
  decision: DecisionRecord;
  count: number;
  expanded: boolean;
  onToggle: () => void;
  skillName?: string;
  onViewSkill?: (name: string) => void;
}) {
  const stateCopy = operationalStateCopy(decision.operationalState);
  const status = decisionStatusCopy(decision);
  const outcome = outcomeCopy(decision.outcome);
  const detail = useDecisionDetail(expanded ? decision.decisionId : null);
  // Truthful headline from the record's own sentence, redundant prefix
  // stripped; the copy.ts state label is the fallback for an empty summary.
  const headline = decisionHeadline(decision, stateCopy.label);
  const subject = decisionSubject(decision);
  const needsApproval = decisionNeedsApproval(decision);

  return (
    <div className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3" data-testid="decision-card">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="niuu:flex niuu:w-full niuu:items-start niuu:justify-between niuu:gap-3 niuu:text-left"
      >
        <span className="niuu:min-w-0">
          <span className="niuu:flex niuu:items-center niuu:gap-2">
            <span className="niuu:min-w-0 niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
              {headline}
            </span>
            {count > 1 ? (
              <span
                data-testid="decision-repeat-count"
                title={`${count} near-identical decisions for this subject`}
                className="niuu:shrink-0 niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-[10px] niuu:text-text-muted"
              >
                ×{count}
              </span>
            ) : null}
          </span>
          <span className="niuu:mt-1 niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-x-1.5 niuu:text-xs niuu:text-text-muted">
            <span>{timeAgo(decision.decidedAt)} ago</span>
            {decision.tier ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-1.5 niuu:py-0.5 niuu:text-[10px]">
                  {decision.tier}
                </span>
              </>
            ) : null}
            <span aria-hidden="true">·</span>
            <span>{formatPercent(decision.confidence)} confidence</span>
            {subject ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="niuu:truncate niuu:font-mono">{subject}</span>
              </>
            ) : null}
          </span>
        </span>
        <span className="niuu:flex niuu:shrink-0 niuu:flex-col niuu:items-end niuu:gap-1 niuu:text-[10px]">
          {outcome ? (
            <span
              className={
                decision.outcome === 'failed'
                  ? 'niuu:rounded-full niuu:bg-critical-bg niuu:px-2 niuu:py-0.5 niuu:text-critical'
                  : 'niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-state-ok'
              }
            >
              {outcome.label}
            </span>
          ) : needsApproval ? (
            <span
              data-testid="decision-needs-approval"
              className="niuu:rounded-full niuu:bg-brand/15 niuu:px-2 niuu:py-0.5 niuu:text-brand"
            >
              {status.label}
            </span>
          ) : (
            <span
              data-testid="decision-status"
              className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-text-muted"
            >
              {status.label}
            </span>
          )}
        </span>
      </button>
      {expanded ? (
        <div
          data-testid={`decision-detail-${decision.decisionId}`}
          className="niuu:mt-3 niuu:border-t niuu:border-border niuu:pt-3 niuu:text-xs"
        >
          <p className="niuu:text-sm niuu:leading-6 niuu:text-text-secondary">
            {decision.rationale}
          </p>
          {skillName && onViewSkill ? (
            <p className="niuu:mt-2 niuu:text-text-muted">
              learned skill: <SkillNameButton name={skillName} onOpen={onViewSkill} />
            </p>
          ) : null}
          {decision.outcomeDetail ? (
            <p className="niuu:mt-2 niuu:text-text-muted">Result: {decision.outcomeDetail}</p>
          ) : null}
          {detail.data ? (
            <div className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-2">
              {detail.data.lineage.signals.length > 0 ? (
                <div>
                  <div className="niuu:font-semibold niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-muted">
                    triggered by
                  </div>
                  {detail.data.lineage.signals.map((signal) => (
                    <p key={signal.signalId} className="niuu:mt-1 niuu:text-text-secondary">
                      {severityCopy(signal.severity).label}: {signal.summary}
                    </p>
                  ))}
                </div>
              ) : null}
              {detail.data.lineage.actions.length > 0 ? (
                <div>
                  <div className="niuu:font-semibold niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-muted">
                    resulting actions
                  </div>
                  {detail.data.lineage.actions.map((action) => (
                    <p key={action.eventId} className="niuu:mt-1 niuu:text-text-secondary">
                      {action.capability.replace(/_/g, ' ')} — {action.status}
                      {action.outcome ? `: ${action.outcome}` : ''}
                    </p>
                  ))}
                </div>
              ) : null}
              {detail.data.lineage.review ? (
                <p className="niuu:text-text-muted">
                  Review item{' '}
                  <a href="/valkyrie/inbox" className="niuu:text-brand">
                    {String(detail.data.lineage.review.status ?? 'pending')} in the inbox
                  </a>
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function DecisionsPanel({
  environmentId,
  skills,
  onViewSkill,
}: {
  environmentId: string;
  skills: LearnedSkillSummary[];
  onViewSkill: (name: string) => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // Fetch only the newest window — the decisions endpoint honours `limit`.
  const { data, isLoading, error } = useDecisionList({
    environmentId,
    limit: LIST_LIMIT,
  });
  const skillNames = skills.map((skill) => skill.skillName);
  // Collapse repeats (same subject re-judged) into one row with a ×N badge.
  const groups: GroupedDecision[] = useMemo(
    () => collapseDecisionsByCorrelation(data?.items ?? []),
    [data?.items],
  );
  const total = data?.total ?? 0;

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-decisions">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <MessageSquare size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">Decisions</h2>
        </div>
        {data ? (
          <span data-testid="decisions-total" className={`niuu:text-xs ${MUTED}`}>
            {total > LIST_LIMIT ? `latest ${LIST_LIMIT} · ${total} total` : `${total} total`}
          </span>
        ) : null}
      </div>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-3">
        {isLoading ? <p className={`niuu:text-sm ${MUTED}`}>Loading decisions...</p> : null}
        {error ? <HistoryUnavailable what="Decision history" /> : null}
        {groups.map(({ decision, count }) => (
          <DecisionCard
            key={decision.decisionId}
            decision={decision}
            count={count}
            expanded={expandedId === decision.decisionId}
            onToggle={() =>
              setExpandedId(expandedId === decision.decisionId ? null : decision.decisionId)
            }
            skillName={referencedSkillName(decision, skillNames)}
            onViewSkill={onViewSkill}
          />
        ))}
        {data && data.items.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>
            No decisions recorded yet. Decisions appear here when a signal crosses the investigation
            threshold or an idle triage runs.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function OperationalStates({ states }: { states: OperationalState[] }) {
  if (states.length === 0) return null;
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-operational-state">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <AlertTriangle size={15} className="niuu:text-state-warn" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Operational state
        </h2>
      </div>
      <div className="niuu:mt-4 niuu:grid niuu:gap-3 niuu:md:grid-cols-2">
        {states.map((state) => (
          <div key={state.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                {state.name}
              </h3>
              <span
                className={`niuu:text-xs ${
                  state.drift === 'major'
                    ? 'niuu:text-critical'
                    : state.drift === 'minor'
                      ? 'niuu:text-state-warn'
                      : 'niuu:text-text-muted'
                }`}
              >
                {state.drift === 'none' ? 'on target' : `${state.drift} drift`}
              </span>
            </div>
            <p className="niuu:mt-2 niuu:text-sm niuu:text-text-secondary">{state.observed}</p>
            <p className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">desired: {state.desired}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function ObservedSignals({ environmentId }: { environmentId: string }) {
  const [browsing, setBrowsing] = useState(false);
  return (
    <section className={PANEL_PAD}>
      <button
        type="button"
        onClick={() => setBrowsing((value) => !value)}
        className="niuu:flex niuu:items-center niuu:gap-1 niuu:text-xs niuu:text-brand"
      >
        <ChevronDown
          size={13}
          aria-hidden="true"
          className={browsing ? 'niuu:rotate-180' : undefined}
        />
        {browsing ? 'Hide observed signals' : 'Browse observed signals'}
      </button>
      {browsing ? <SignalBrowser environmentId={environmentId} /> : null}
    </section>
  );
}

/** The record behind the Activity stories, for one environment or all of them. */
export function ResidentHistory({
  environmentId,
  operationalStates,
  onViewSkill,
}: {
  environmentId: string;
  operationalStates: OperationalState[];
  onViewSkill: (environmentId: string, name: string) => void;
}) {
  const skillsQuery = useValkyrieSkills(environmentId, Boolean(environmentId));
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3">
      <OperationalStates
        states={operationalStates.filter(
          (state) => !environmentId || state.environmentId === environmentId,
        )}
      />
      <EvolutionFold id="decisions" label="Decision history">
        <DecisionsPanel
          environmentId={environmentId}
          skills={skillsQuery.data ?? []}
          onViewSkill={(name) => onViewSkill(environmentId, name)}
        />
      </EvolutionFold>
      <ObservedSignals environmentId={environmentId} />
    </div>
  );
}
