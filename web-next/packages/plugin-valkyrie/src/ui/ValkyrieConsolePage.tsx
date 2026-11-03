import { useMemo, useState } from 'react';
import { Shield } from 'lucide-react';
import { Meter } from '@niuulabs/ui';
import { EvolutionFold, EvolutionPanel } from './EvolutionPanel';
import { OPERATOR_PARTICIPANT_ID, ResidentHeader } from './ResidentHeader';
import {
  autonomyModeForLevel,
  dreamCurationBacklog,
  dreamLessonsCurated,
  dreamProposalsFiled,
  environmentKindLabel,
  formatDreamDuration,
  latestBuildGrant,
  learningFeedbackVerdictLabel,
  realmSlugForEnvironment,
  LEARNING_SCOPE_ORDER,
  LIST_LIMIT,
  type AutonomyMode,
  type DreamOutcome,
  type EvolutionReport,
  type LearnedSkillSummary,
  type LearningRecord,
  type LearningScope,
  type RealmTrustGrant,
  type SkillUsageStat,
  type ValkyrieDashboard,
  type ValkyrieResident,
} from '../domain';
import { useRealms, useRealmTrustGrants } from '../application/useRealmGovernance';
import { useUpdateAutonomy, useValkyrieDashboard } from '../application/useValkyrieDashboard';
import { useDecisionList, useSkillStats } from '../application/useValkyrieHistory';
import { useReviewList } from '../application/useReviews';
import { useValkyrieSkills } from '../application/useValkyrieSkills';
import { Roster } from './Roster';
import { LearningViewer } from './LearningViewer';
import { SkillViewer } from './SkillViewer';
import { ToolBuilderGrantCard, type RealmRef } from './ToolBuilderGrantCard';
import { timeAgo } from './reviewFormat';
import {
  actionAuthorityCopy,
  autonomyModeCopy,
  describeIdleSituation,
  dreamStatusCopy,
  retirementReasonCopy,
  reviewKindLabel,
  severityCopy,
} from './copy';

const PANEL =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary';
const PANEL_PAD = `${PANEL} niuu:p-4`;
const FOLD_BODY = 'niuu:pt-3';
const MUTED = 'niuu:text-text-muted';

const AUTONOMY_MODES: AutonomyMode[] = ['guarded', 'autonomous', 'yolo'];
const REVIEW_PREVIEW_COUNT = 3;

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function taskSeverities(
  valkyrie: ValkyrieResident,
  dashboard: ValkyrieDashboard,
): string[] | undefined {
  if (valkyrie.signalTaskSeverities?.length) return valkyrie.signalTaskSeverities;
  const runtime = dashboard.telemetry?.runtime?.find(
    (entry) => entry.environmentId === valkyrie.environmentId,
  );
  return runtime?.signalTaskSeverities;
}

function ConsoleLoading() {
  return (
    <div data-testid="valkyrie-console-loading" className={`niuu:p-6 niuu:text-sm ${MUTED}`}>
      Loading resident console...
    </div>
  );
}

function ConsoleError({ message }: { message: string }) {
  return (
    <div
      data-testid="valkyrie-console-error"
      role="alert"
      className="niuu:m-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
    >
      {message}
    </div>
  );
}

function EmptyConsole() {
  return (
    <div data-testid="valkyrie-console-empty" className={`${PANEL} niuu:m-4 niuu:p-6 ${MUTED}`}>
      No residents have announced yet.
    </div>
  );
}

/**
 * How the realm's build grant shapes what a resident may actually do:
 * the most recent `build` trust grant wins (level → mode); the statically
 * configured mode is only the fallback when no grant exists. While the
 * grant list is loading or failed (`grantsKnown` false) the page shows the
 * configured mode without claiming it is effective.
 */
interface BuildGovernance {
  realm: RealmRef;
  /** Whether a realm actually exists for this environment's slug. */
  realmExists: boolean;
  buildGrant: RealmTrustGrant | null;
  grantsKnown: boolean;
  effectiveMode: AutonomyMode;
}

function AuthorityPanel({
  valkyrie,
  severities,
  governance,
}: {
  valkyrie: ValkyrieResident;
  severities: string[] | undefined;
  governance: BuildGovernance;
}) {
  const modeCopy = autonomyModeCopy(valkyrie.autonomyMode);
  const { realm, realmExists, buildGrant, grantsKnown, effectiveMode } = governance;
  const effectiveCopy = autonomyModeCopy(effectiveMode);
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-authority">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Shield size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Authority & autonomy
        </h2>
      </div>
      <div className="niuu:mt-5 niuu:grid niuu:grid-cols-3 niuu:overflow-hidden niuu:rounded-md niuu:border niuu:border-border niuu:text-center niuu:text-xs">
        {AUTONOMY_MODES.map((mode) => (
          <div
            key={mode}
            className={`niuu:px-3 niuu:py-2 ${
              mode === effectiveMode
                ? 'niuu:bg-brand/25 niuu:text-text-primary'
                : 'niuu:bg-bg-primary niuu:text-text-muted'
            }`}
          >
            {autonomyModeCopy(mode).label}
          </div>
        ))}
      </div>
      {grantsKnown ? (
        <p data-testid="valkyrie-autonomy-provenance" className={`niuu:mt-2 niuu:text-xs ${MUTED}`}>
          {buildGrant
            ? `${effectiveCopy.label} is the effective build autonomy — workspace grant level ` +
              `${buildGrant.level} on ${realm.slug}; configured mode is ${modeCopy.label.toLowerCase()}.`
            : `No build grant on workspace ${realm.slug} yet — the configured mode ` +
              `${modeCopy.label.toLowerCase()} applies.`}
        </p>
      ) : null}
      <p className="niuu:mt-4 niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:text-sm niuu:leading-6 niuu:text-text-secondary">
        {effectiveCopy.description}
      </p>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-2 niuu:text-xs">
        {['autonomous', 'court_required', 'human_review_required'].map((authority) => {
          const copy = actionAuthorityCopy(authority);
          return (
            <div key={authority} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-2">
              <span className="niuu:font-semibold niuu:text-text-primary">{copy.label}</span>
              <span className="niuu:text-text-muted"> — {copy.description}</span>
            </div>
          );
        })}
      </div>
      <div className="niuu:mt-4">
        <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
          investigation threshold
        </div>
        <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
          Signals at these severities start an investigation task; everything else is batched into
          idle triage.
        </p>
        <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:gap-2">
          {(severities ?? ['warning', 'critical']).map((severity) => (
            <span
              key={severity}
              className="niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-1 niuu:text-xs niuu:text-text-secondary"
            >
              {severityCopy(severity).label}
            </span>
          ))}
        </div>
      </div>
      <div className="niuu:mt-4" data-testid="valkyrie-realm-governance">
        <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
          workspace build governance — {realm.slug}
        </div>
        {realmExists ? (
          <>
            <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
              The workspace&apos;s build grant pins the workflow this resident uses to build tools
              and sets its effective build autonomy.
            </p>
            <ToolBuilderGrantCard realm={realm} />
          </>
        ) : (
          <p
            data-testid="valkyrie-realm-unconfigured"
            className={`niuu:mt-1 niuu:text-xs ${MUTED}`}
          >
            No workspace is configured for {realm.slug}, so there is no build governance to set. The
            configured autonomy mode applies until a workspace is created.
          </p>
        )}
      </div>
    </section>
  );
}

/** One upgraded learning card: lifecycle, confidence, feedback, provenance. */
function LearningCard({
  learning,
  onOpen,
}: {
  learning: LearningRecord;
  onOpen: (learningId: string) => void;
}) {
  return (
    <button
      type="button"
      data-testid={`learning-card-${learning.id}`}
      onClick={() => onOpen(learning.id)}
      className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-transparent niuu:bg-bg-primary niuu:p-3 niuu:text-left niuu:hover:border-brand/70"
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
        <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">{learning.title}</span>
        <span className="niuu:shrink-0 niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-[10px] niuu:text-brand">
          {learning.status.replace(/_/g, ' ')}
        </span>
      </div>
      <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{learning.summary}</p>
      <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:text-[10px] niuu:text-text-secondary">
        <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5">
          {learning.scope}
        </span>
        <span className="niuu:flex niuu:items-center niuu:gap-1.5">
          <Meter used={learning.confidence} limit={1} tone="steady" className="niuu:w-16" />
          <span className="niuu:font-mono">{formatPercent(learning.confidence)}</span>
        </span>
        {learning.repetition != null && learning.repetition > 1 ? (
          <span data-testid={`learning-repetition-${learning.id}`}>
            × {learning.repetition} seen
          </span>
        ) : null}
        <span
          data-testid={`learning-fb-${learning.id}`}
          className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5"
        >
          fb:{' '}
          {learning.feedback
            ? learningFeedbackVerdictLabel(learning.feedback.verdict).toLowerCase()
            : 'awaiting'}
        </span>
      </div>
      <p className="niuu:mt-1 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
        {learning.sourceValkyrieId} · {learning.sourceEnvironmentId}
      </p>
      <p className="niuu:mt-1 niuu:text-[10px] niuu:text-text-muted">{learning.evaluation}</p>
      <span
        aria-hidden="true"
        className="niuu:mt-1 niuu:inline-block niuu:text-[10px] niuu:text-brand"
      >
        open →
      </span>
    </button>
  );
}

function LearningPanel({
  dashboard,
  valkyrie,
  onOpenLearning,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
  onOpenLearning: (learningId: string) => void;
}) {
  const [scopeFilter, setScopeFilter] = useState<LearningScope | null>(null);
  const relevantLearnings = dashboard.learnings.filter(
    (learning) =>
      learning.sourceEnvironmentId === valkyrie.environmentId ||
      learning.sourceValkyrieId === valkyrie.id,
  );
  const scopeCounts = new Map<LearningScope, number>();
  for (const learning of relevantLearnings) {
    scopeCounts.set(learning.scope, (scopeCounts.get(learning.scope) ?? 0) + 1);
  }
  const learnings = (
    scopeFilter
      ? relevantLearnings.filter((learning) => learning.scope === scopeFilter)
      : relevantLearnings
  ).slice(0, LIST_LIMIT);
  const toolNeeds = (dashboard.telemetry?.recentToolNeeds ?? [])
    .filter((need) => need.environmentId === valkyrie.environmentId)
    .slice(0, 4);
  return (
    <section className={FOLD_BODY} data-testid="valkyrie-learning">
      <div
        data-testid="learning-scope-filters"
        className="niuu:mt-3 niuu:flex niuu:flex-wrap niuu:gap-1.5"
      >
        {LEARNING_SCOPE_ORDER.map((scope) => {
          const active = scopeFilter === scope;
          return (
            <button
              key={scope}
              type="button"
              data-testid={`learning-scope-filter-${scope}`}
              aria-pressed={active}
              onClick={() => setScopeFilter(active ? null : scope)}
              className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-2 niuu:py-0.5 niuu:text-[10px] ${
                active
                  ? 'niuu:border-brand niuu:bg-brand/10 niuu:text-brand'
                  : 'niuu:border-border niuu:bg-bg-primary niuu:text-text-secondary'
              }`}
            >
              {scope}{' '}
              <span className="niuu:font-mono niuu:text-text-muted">
                {scopeCounts.get(scope) ?? 0}
              </span>
            </button>
          );
        })}
      </div>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-3">
        {toolNeeds.map((need) => (
          <div
            key={need.id}
            className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:font-mono niuu:text-sm niuu:text-brand">{need.capability}</div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary">{need.summary}</p>
          </div>
        ))}
        {learnings.map((learning) => (
          <LearningCard key={learning.id} learning={learning} onOpen={onOpenLearning} />
        ))}
        {toolNeeds.length === 0 && learnings.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>No tool gaps or lessons recorded.</p>
        ) : null}
      </div>
    </section>
  );
}

function LearnedSkillsPanel({
  skills,
  skillStats,
  isLoading,
  error,
  onViewSkill,
}: {
  skills: LearnedSkillSummary[];
  skillStats: SkillUsageStat[];
  isLoading: boolean;
  error: unknown;
  onViewSkill: (name: string) => void;
}) {
  return (
    <section className={FOLD_BODY} data-testid="valkyrie-learned-skills">
      <div className="niuu:flex niuu:flex-col niuu:gap-2">
        {isLoading ? <p className={`niuu:text-sm ${MUTED}`}>Loading learned skills...</p> : null}
        {error ? (
          <p
            data-testid="valkyrie-skills-unavailable"
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:text-xs niuu:text-critical"
          >
            Learned skills could not be loaded — this API backend does not serve the skills endpoint
            yet. Redeploy the platform API with the current build to enable it.
          </p>
        ) : null}
        {skills.map((skill) => {
          const usage = skillStats.find(
            (row) => row.environmentId === skill.environmentId && row.skillName === skill.skillName,
          );
          const inventoryAge = skill.observedAt ? ` · seen ${timeAgo(skill.observedAt)} ago` : '';
          return (
            <button
              key={skill.skillName}
              type="button"
              data-testid={`learned-skill-${skill.skillName}`}
              onClick={() => onViewSkill(skill.skillName)}
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-transparent niuu:bg-bg-primary niuu:p-3 niuu:text-left niuu:hover:border-brand/70"
            >
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <span className="niuu:truncate niuu:font-mono niuu:text-sm niuu:text-brand">
                  {skill.skillName}
                </span>
                <span className="niuu:shrink-0 niuu:text-[10px] niuu:text-text-muted">
                  {skill.adoptedAt
                    ? `installed ${timeAgo(skill.adoptedAt)} ago`
                    : `installed${inventoryAge}`}
                </span>
              </div>
              <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{skill.description}</p>
              <p className="niuu:mt-2 niuu:text-[10px] niuu:text-text-muted">
                {skill.hasCode ? 'executable tool' : 'guidance'} ·{' '}
                {usage
                  ? `${usage.uses} recorded use${usage.uses === 1 ? '' : 's'} · ${usage.successes} succeeded · ${usage.failures} failed`
                  : 'no recorded use'}
              </p>
            </button>
          );
        })}
        {!isLoading && !error && skills.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>
            No learned skills installed on this environment yet.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
      <div className={`niuu:text-xs ${MUTED}`}>{label}</div>
      <div className="niuu:mt-1 niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
        {value}
      </div>
      {detail ? <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>{detail}</p> : null}
    </div>
  );
}

function DreamList({ label, items, testId }: { label: string; items: string[]; testId: string }) {
  if (items.length === 0) return null;
  return (
    <div data-testid={testId}>
      <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-muted">
        {label} · {items.length}
      </div>
      <ul className="niuu:mt-2 niuu:flex niuu:flex-col niuu:gap-1">
        {items.map((item) => (
          <li
            key={item}
            className="niuu:truncate niuu:font-mono niuu:text-xs niuu:text-text-secondary"
            title={item}
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RetiredToolsList({ dream }: { dream: DreamOutcome }) {
  const retired = dream.learnedTools?.retired ?? [];
  if (retired.length === 0) return null;
  return (
    <div data-testid="dream-retired-tools">
      <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-muted">
        Learned tools retired · {retired.length}
      </div>
      <ul className="niuu:mt-2 niuu:flex niuu:flex-col niuu:gap-2">
        {retired.map((tool) => {
          const reason = retirementReasonCopy(tool.reason);
          return (
            <li key={tool.name} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-2">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <span className="niuu:truncate niuu:font-mono niuu:text-xs niuu:text-brand">
                  {tool.name}
                </span>
                <span
                  title={reason.description}
                  className="niuu:shrink-0 niuu:text-[10px] niuu:text-text-muted"
                >
                  {reason.label}
                </span>
              </div>
              {tool.supersededBy ? (
                <p className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary">
                  replaced by <span className="niuu:font-mono">{tool.supersededBy}</span>
                </p>
              ) : null}
              {tool.detail ? (
                <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{tool.detail}</p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function retiredByReasonDetail(dream: DreamOutcome): string | undefined {
  const byReason = Object.entries(dream.learnedTools?.retiredByReason ?? {});
  if (byReason.length === 0) return undefined;
  return byReason
    .map(([reason, count]) => `${count} ${retirementReasonCopy(reason).label.toLowerCase()}`)
    .join(' · ');
}

/** The resident's last consolidation dream: when, how long, and what it changed. */
export function DreamPanel({ valkyrie }: { valkyrie: ValkyrieResident }) {
  const dream = valkyrie.lastDream;
  const status = dream ? dreamStatusCopy(dream.status) : null;
  const failed = dream?.status === 'failed';
  return (
    <section className={FOLD_BODY} data-testid="valkyrie-last-dream">
      <div className="niuu:flex niuu:items-center niuu:justify-end niuu:gap-2">
        {dream && status ? (
          <span
            data-testid="dream-status"
            title={status.description}
            className={`niuu:text-xs ${failed ? 'niuu:text-critical' : MUTED}`}
          >
            {status.label} · {timeAgo(dream.finishedAt)} ago
          </span>
        ) : null}
      </div>
      {!dream ? (
        <p className={`niuu:mt-4 niuu:text-sm ${MUTED}`}>
          {valkyrie.wakefulness === 'dreaming'
            ? 'Dreaming now — no earlier dream has been reported.'
            : 'No finished consolidation dream has been reported by this resident yet.'}
        </p>
      ) : (
        <>
          {failed ? (
            <p
              role="alert"
              data-testid="dream-error"
              className="niuu:mt-4 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:font-mono niuu:text-xs niuu:text-critical"
            >
              {dream.error || 'The dream failed without reporting an error.'}
            </p>
          ) : null}
          <div className="niuu:mt-4 niuu:grid niuu:gap-3 niuu:md:grid-cols-5">
            <Metric
              label="ran for"
              value={formatDreamDuration(dream.durationSeconds)}
              detail={`finished ${new Date(dream.finishedAt).toLocaleString()}`}
            />
            <Metric
              label="skills"
              value={`${dream.skills.promoted.length} promoted · ${dream.skills.markedStale.length} stale`}
              detail={`${dream.skills.reviewed} reviewed`}
            />
            <Metric
              label="lessons curated"
              value={dream.lessons ? String(dreamLessonsCurated(dream)) : 'not due'}
              detail={
                dream.lessons
                  ? [
                      `${dream.lessons.merged.length} merged of ${dream.lessons.reviewed} reviewed`,
                      dreamCurationBacklog(dream),
                    ]
                      .filter(Boolean)
                      .join(' · ')
                  : 'lesson curation did not run this dream'
              }
            />
            <Metric
              label="tools retired"
              value={dream.learnedTools ? String(dream.learnedTools.retired.length) : 'not curated'}
              detail={
                dream.learnedTools
                  ? (retiredByReasonDetail(dream) ??
                    `${dream.learnedTools.installedBefore} installed, none retired`)
                  : 'no learned-tool curator on this resident'
              }
            />
            <Metric
              label="proposals filed"
              value={String(dreamProposalsFiled(dream))}
              detail={`${dream.skills.promotionCandidates.length} held promotions · ${
                dream.selfImprovement?.actions.length ?? 0
              } improvements`}
            />
          </div>
          <div className="niuu:mt-4 niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
            <RetiredToolsList dream={dream} />
            <DreamList
              label="Learned tools restored"
              items={dream.learnedTools?.restored ?? []}
              testId="dream-restored-tools"
            />
            <DreamList
              label="Lessons merged"
              items={dream.lessons?.merged ?? []}
              testId="dream-lessons-merged"
            />
            <DreamList
              label="Lessons superseded"
              items={dream.lessons?.superseded ?? []}
              testId="dream-lessons-superseded"
            />
            <DreamList
              label="Lessons pruned"
              items={dream.lessons?.pruned ?? []}
              testId="dream-lessons-pruned"
            />
            <DreamList
              label="Lessons rewritten"
              items={dream.lessons?.rewritten ?? []}
              testId="dream-lessons-rewritten"
            />
            <DreamList
              label="Lessons promoted"
              items={dream.lessons?.promoted ?? []}
              testId="dream-lessons-promoted"
            />
            <DreamList
              label="Skills promoted"
              items={dream.skills.promoted}
              testId="dream-skills-promoted"
            />
            <DreamList
              label="Skills marked stale"
              items={dream.skills.markedStale}
              testId="dream-skills-stale"
            />
            <DreamList
              label="Promotions held for review"
              items={dream.skills.promotionCandidates}
              testId="dream-promotions-held"
            />
            <DreamList
              label="Self-improvement actions"
              items={dream.selfImprovement?.actions ?? []}
              testId="dream-improvement-actions"
            />
          </div>
        </>
      )}
    </section>
  );
}

function NeedsYou({
  valkyrie,
  report,
  hasDecisions,
  signalTotal,
  severities,
}: {
  valkyrie: ValkyrieResident;
  report: EvolutionReport | null;
  hasDecisions: boolean;
  signalTotal: number;
  severities: string[] | undefined;
}) {
  const { data } = useReviewList({ status: 'pending' });
  const pending = (data ?? []).filter(
    (item) => item.environmentId === valkyrie.environmentId || item.valkyrieId === valkyrie.id,
  );
  const failing = report?.attention ?? [];
  const dream = valkyrie.lastDream;
  const dreamFailed = dream?.status === 'failed';
  const clear = pending.length === 0 && failing.length === 0 && !dreamFailed;
  const activity = `/valkyrie/activity?environment=${encodeURIComponent(valkyrie.environmentId)}`;

  return (
    <section data-testid="valkyrie-pending-reviews">
      {clear ? (
        <p className="niuu:text-base niuu:text-text-primary">Nothing needs you right now.</p>
      ) : (
        <ul data-testid="valkyrie-needs-you" className="niuu:flex niuu:flex-col niuu:gap-2">
          {pending.slice(0, REVIEW_PREVIEW_COUNT).map((item) => (
            <li key={item.itemId}>
              <a
                href="/valkyrie/inbox"
                className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:rounded-md niuu:bg-bg-secondary niuu:p-3"
              >
                <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                  {item.title}
                </span>
                <span className="niuu:shrink-0 niuu:text-xs niuu:text-text-muted">
                  {reviewKindLabel(item.kind)}
                </span>
              </a>
            </li>
          ))}
          {pending.length > REVIEW_PREVIEW_COUNT ? (
            <li className={`niuu:text-xs ${MUTED}`}>
              <a href="/valkyrie/inbox" className="niuu:text-brand">
                and {pending.length - REVIEW_PREVIEW_COUNT} more in the inbox
              </a>
            </li>
          ) : null}
          {failing.map((item) => (
            <li
              key={item.tool}
              data-testid="evolution-attention"
              className="niuu:rounded-md niuu:bg-critical-bg niuu:p-3 niuu:text-sm niuu:text-critical"
            >
              <span className="niuu:font-mono niuu:text-xs">{item.tool}</span> failed all{' '}
              {item.runs} runs and is still installed.
            </li>
          ))}
          {dreamFailed ? (
            <li
              role="alert"
              data-testid="evolution-dream-failed"
              className="niuu:rounded-md niuu:bg-critical-bg niuu:p-3 niuu:font-mono niuu:text-xs niuu:text-critical"
            >
              {dream?.error || 'The dream failed without reporting an error.'}
            </li>
          ) : null}
        </ul>
      )}
      <p data-testid="valkyrie-now" className={`niuu:mt-1 niuu:text-sm ${MUTED}`}>
        {hasDecisions
          ? `Last action ${valkyrie.lastActionAt ? `${timeAgo(valkyrie.lastActionAt)} ago` : 'unknown'}.`
          : describeIdleSituation(signalTotal, severities)}{' '}
        <a href={activity} className="niuu:text-brand">
          See activity
        </a>
      </p>
    </section>
  );
}

function Purpose({
  dashboard,
  valkyrie,
  governance,
  severities,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
  governance: BuildGovernance;
  severities: string[] | undefined;
}) {
  const environment = dashboard.environments.find((entry) => entry.id === valkyrie.environmentId);
  const flock = dashboard.flocks.find(
    (entry) => entry.id === valkyrie.flockId || entry.valkyrieIds.includes(valkyrie.id),
  );
  const updateAutonomy = useUpdateAutonomy();
  const { buildGrant, grantsKnown } = governance;
  const effectiveCopy = autonomyModeCopy(governance.effectiveMode);
  const chip = 'niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary';
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3 niuu:pt-3">
      {valkyrie.charter ? (
        <p
          data-testid="valkyrie-charter"
          className="niuu:text-sm niuu:leading-6 niuu:text-text-primary"
        >
          {valkyrie.charter}
        </p>
      ) : null}
      <div
        data-testid="valkyrie-identity"
        className="niuu:flex niuu:flex-wrap niuu:gap-2 niuu:text-xs"
      >
        <span data-testid="valkyrie-effective-autonomy" className={chip}>
          {effectiveCopy.label}
          {buildGrant
            ? ` · effective (workspace grant level ${buildGrant.level})`
            : grantsKnown
              ? ' · configured (no workspace build grant)'
              : ''}
        </span>
        {buildGrant ? (
          <span data-testid="valkyrie-configured-autonomy" className={chip}>
            configured {autonomyModeCopy(valkyrie.autonomyMode).label.toLowerCase()}
          </span>
        ) : null}
        <span className={chip}>
          {valkyrie.persona} · {valkyrie.specialty}
        </span>
        {environment ? (
          <span className={chip}>{environmentKindLabel(environment.kind)}</span>
        ) : null}
        {flock ? <span className={chip}>flock {flock.name}</span> : null}
        <span className={chip}>workspace {governance.realm.slug}</span>
        <span className={chip}>judgment confidence {formatPercent(valkyrie.confidence)}</span>
      </div>
      <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
        autonomy
        <select
          aria-label={`Autonomy mode for ${valkyrie.name}`}
          value={valkyrie.autonomyMode}
          disabled={updateAutonomy.isPending}
          onChange={(event) =>
            updateAutonomy.mutate({
              valkyrieId: valkyrie.id,
              mode: event.target.value as AutonomyMode,
              reason: 'Operator change from the resident console',
              participantId: OPERATOR_PARTICIPANT_ID,
            })
          }
          className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
        >
          {AUTONOMY_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {mode}
            </option>
          ))}
        </select>
        <span>Configured mode. The workspace build grant still wins for tool builds.</span>
      </label>
      {updateAutonomy.isError ? (
        <p role="alert" className="niuu:text-xs niuu:text-critical">
          {updateAutonomy.error instanceof Error
            ? updateAutonomy.error.message
            : 'Unable to change autonomy'}
        </p>
      ) : null}
      <AuthorityPanel valkyrie={valkyrie} severities={severities} governance={governance} />
    </div>
  );
}

function Console({ dashboard }: { dashboard: ValkyrieDashboard }) {
  const [selectedId, setSelectedId] = useState(() => dashboard.valkyries[0]?.id ?? '');
  const [viewedSkillName, setViewedSkillName] = useState<string | null>(null);
  const [viewedLearningId, setViewedLearningId] = useState<string | null>(null);
  const selected =
    dashboard.valkyries.find((entry) => entry.id === selectedId) ?? dashboard.valkyries[0];
  const environmentSignals = useMemo(
    () =>
      dashboard.signals.filter(
        (signal) => !selected || signal.environmentId === selected.environmentId,
      ),
    [dashboard.signals, selected],
  );
  const decisionsQuery = useDecisionList(
    { environmentId: selected?.environmentId ?? '', limit: LIST_LIMIT },
    Boolean(selected),
  );
  const skillsQuery = useValkyrieSkills(selected?.environmentId ?? '', Boolean(selected));
  const skillStatsQuery = useSkillStats(selected?.environmentId ?? '');
  // A realm's slug IS the environment's raw id; its build grant governs
  // what this resident may build, ahead of the configured autonomy mode.
  const realmSlug = realmSlugForEnvironment(selected?.environmentId ?? '');
  // Fetch the realm list once and only ask for trust grants when this env's
  // slug is a real realm — otherwise the grants request 404s for every
  // environment that has no realm configured. While realms are still loading
  // we hold the grants request; a failed realms list means "unknown", so we
  // also hold rather than spam.
  const realmsQuery = useRealms();
  const realmExists = (realmsQuery.data ?? []).some((realm) => realm.slug === realmSlug);
  const grantsQuery = useRealmTrustGrants(realmSlug, realmExists);

  if (!selected) return <EmptyConsole />;

  const selectResident = (valkyrieId: string) => {
    setSelectedId(valkyrieId);
    setViewedSkillName(null);
    setViewedLearningId(null);
  };
  const skills = skillsQuery.data ?? [];
  const report = selected.lastDream?.evolution ?? null;
  const grantsKnown = realmExists && grantsQuery.isSuccess;
  const buildGrant = grantsKnown ? latestBuildGrant(grantsQuery.data ?? []) : null;

  const severities = taskSeverities(selected, dashboard);
  const decisions = decisionsQuery.data?.items ?? [];
  const environmentSummary = dashboard.environments.find(
    (entry) => entry.id === selected.environmentId,
  );
  // Live poll telemetry is the truth for "how much has this resident seen";
  // the environment summary count is a configured value that can lag at 0.
  const collectedSignals =
    dashboard.telemetry?.byEnvironment?.find(
      (entry) => entry.environmentId === selected.environmentId,
    )?.signalsCollected ?? 0;
  const signalTotal = Math.max(
    environmentSummary?.signalCount ?? 0,
    collectedSignals,
    environmentSignals.length,
  );
  const governance: BuildGovernance = {
    realm: { slug: realmSlug, name: environmentSummary?.name ?? realmSlug },
    realmExists,
    buildGrant,
    grantsKnown,
    effectiveMode: buildGrant ? autonomyModeForLevel(buildGrant.level) : selected.autonomyMode,
  };

  return (
    <div className="niuu:grid niuu:h-full niuu:min-h-0 niuu:grid-cols-[320px_minmax(0,1fr)] niuu:bg-bg-primary">
      <Roster dashboard={dashboard} selectedId={selected.id} onSelect={selectResident} />
      <main className="niuu:min-h-0 niuu:overflow-auto niuu:p-6">
        <div className="niuu:flex niuu:max-w-3xl niuu:flex-col niuu:gap-6">
          <ResidentHeader
            dashboard={dashboard}
            valkyrie={selected}
            autonomy={autonomyModeCopy(governance.effectiveMode).label}
          />
          <NeedsYou
            valkyrie={selected}
            report={report}
            hasDecisions={decisions.length > 0}
            signalTotal={signalTotal}
            severities={severities}
          />
          <EvolutionPanel dashboard={dashboard} report={report} onViewSkill={setViewedSkillName} />
          <div>
            <EvolutionFold id="tools" label={`All ${skills.length} tools and lessons`}>
              <LearnedSkillsPanel
                skills={skills}
                skillStats={skillStatsQuery.data ?? []}
                isLoading={skillsQuery.isLoading}
                error={skillsQuery.error}
                onViewSkill={setViewedSkillName}
              />
              <LearningPanel
                dashboard={dashboard}
                valkyrie={selected}
                onOpenLearning={setViewedLearningId}
              />
              <DreamPanel valkyrie={selected} />
            </EvolutionFold>
            <EvolutionFold id="purpose" label="Purpose and permissions">
              <Purpose
                dashboard={dashboard}
                valkyrie={selected}
                governance={governance}
                severities={severities}
              />
            </EvolutionFold>
          </div>
        </div>
      </main>
      <SkillViewer
        environmentId={selected.environmentId}
        skillName={viewedSkillName}
        onClose={() => setViewedSkillName(null)}
      />
      <LearningViewer
        learningId={viewedLearningId}
        onClose={() => setViewedLearningId(null)}
        onNavigate={setViewedLearningId}
      />
    </div>
  );
}

export function ValkyrieConsolePage() {
  const { data, isLoading, error } = useValkyrieDashboard();

  if (isLoading) return <ConsoleLoading />;
  if (error) {
    return (
      <ConsoleError
        message={error instanceof Error ? error.message : 'Unable to load resident console'}
      />
    );
  }
  if (!data || data.valkyries.length === 0) return <EmptyConsole />;

  return (
    <div data-testid="valkyrie-console-page" className="niuu:h-full niuu:min-h-0">
      <Console dashboard={data} />
    </div>
  );
}
