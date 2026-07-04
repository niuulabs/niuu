import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  Brain,
  ChevronDown,
  ChevronRight,
  GraduationCap,
  Inbox,
  Mail,
  MessageSquare,
  Settings2,
  Shield,
  Sparkles,
  Users,
  Zap,
} from 'lucide-react';
import { Meter } from '@niuulabs/ui';
import {
  autonomyModeForLevel,
  collapseDecisionsByCorrelation,
  decisionHeadline,
  decisionNeedsApproval,
  decisionSubject,
  environmentKindLabel,
  latestBuildGrant,
  learningFeedbackVerdictLabel,
  openHuddleForEnvironment,
  realmSlugForEnvironment,
  referencedSkillName,
  valkyrieLastSeenAt,
  LEARNING_SCOPE_ORDER,
  LIST_LIMIT,
  type AutonomyMode,
  type DecisionRecord,
  type GroupedDecision,
  type HuddleSummary,
  type LearnedSkillSummary,
  type LearningRecord,
  type LearningScope,
  type RealmTrustGrant,
  type ValkyrieDashboard,
  type ValkyrieEventTelemetry,
  type ValkyrieResident,
} from '../domain';
import { useRealms, useRealmTrustGrants } from '../application/useRealmGovernance';
import {
  useJoinHuddle,
  useLeaveHuddle,
  useSendHuddleMessage,
  useUpdateAutonomy,
  useValkyrieDashboard,
} from '../application/useValkyrieDashboard';
import {
  useDecisionDetail,
  useDecisionList,
  useSignalHistory,
  useSkillStats,
} from '../application/useValkyrieHistory';
import { useReviewList } from '../application/useReviews';
import { useValkyrieSkills } from '../application/useValkyrieSkills';
import { healthClasses, Roster, wakefulnessIcon } from './Roster';
import { LearningViewer } from './LearningViewer';
import { SkillNameButton, SkillViewer } from './SkillViewer';
import { ToolBuilderGrantCard, type RealmRef } from './ToolBuilderGrantCard';
import { timeAgo } from './reviewFormat';
import {
  actionAuthorityCopy,
  autonomyModeCopy,
  autonomyModeHint,
  decisionStatusCopy,
  describeIdleSituation,
  errorMessage,
  isLearnedSkillState,
  operationalStateCopy,
  outcomeCopy,
  reviewKindLabel,
  severityCopy,
  wakefulnessCopy,
} from './copy';

const PANEL =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary';
const PANEL_PAD = `${PANEL} niuu:p-4`;
const MUTED = 'niuu:text-text-muted';

const AUTONOMY_MODES: AutonomyMode[] = ['guarded', 'autonomous', 'yolo'];
const TIMELINE_PREVIEW_COUNT = 7;
const SIGNAL_PAGE_SIZE = 10;

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function eventTone(kind: ValkyrieEventTelemetry['kind']): string {
  if (kind === 'action') return 'niuu:border-state-warn niuu:text-state-warn';
  if (kind === 'judgment') return 'niuu:border-brand niuu:text-brand';
  if (kind === 'signal') return 'niuu:border-state-ok niuu:text-state-ok';
  if (kind === 'learning') return 'niuu:border-purple-400 niuu:text-purple-300';
  return 'niuu:border-border niuu:text-text-muted';
}

function newestTimestamp(values: Array<string | undefined>): string | undefined {
  return values
    .filter((value): value is string => Boolean(value))
    .sort((a, b) => b.localeCompare(a))[0];
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
      Loading Valkyrie console...
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
      No resident Valkyries have announced yet.
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

const OPERATOR_PARTICIPANT_ID = 'human:operator';

const HERO_BUTTON =
  'niuu:flex niuu:items-center niuu:gap-1.5 niuu:rounded-md niuu:border niuu:border-solid ' +
  'niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-xs ' +
  'niuu:text-text-primary niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50';

/**
 * Join/Leave the environment's huddle and direct-message the resident via
 * the existing huddle APIs. Both need an open huddle — without one the
 * buttons stay disabled and say why.
 */
function HeroActions({
  valkyrie,
  huddle,
  onCompose,
  composing,
  onToggleAutonomy,
  changingAutonomy,
}: {
  valkyrie: ValkyrieResident;
  huddle?: HuddleSummary;
  onCompose: () => void;
  composing: boolean;
  onToggleAutonomy: () => void;
  changingAutonomy: boolean;
}) {
  const joinHuddle = useJoinHuddle();
  const leaveHuddle = useLeaveHuddle();
  const joined = huddle?.joined ?? false;
  const huddleTitle = huddle ? huddle.title : 'No open huddle for this environment right now';
  // The backend requires a joined participant before it accepts messages.
  const messageTitle = !huddle
    ? huddleTitle
    : joined
      ? `Message ${valkyrie.name} in ${huddle.title}`
      : `Join ${huddle.title} first`;
  const huddleError = joinHuddle.error ?? leaveHuddle.error;

  return (
    <div className="niuu:flex niuu:max-w-md niuu:flex-wrap niuu:items-center niuu:justify-end niuu:gap-2">
      <button
        type="button"
        data-testid="valkyrie-join-huddle"
        title={huddleTitle}
        disabled={!huddle || joinHuddle.isPending || leaveHuddle.isPending}
        onClick={() => {
          if (!huddle) return;
          if (joined) {
            leaveHuddle.mutate(huddle.id);
            return;
          }
          joinHuddle.mutate({
            huddleId: huddle.id,
            participantId: OPERATOR_PARTICIPANT_ID,
            displayName: 'Operator',
            action: 'observe',
          });
        }}
        className={HERO_BUTTON}
      >
        <Users size={13} aria-hidden="true" />
        {joined ? 'Leave huddle' : 'Join huddle'}
      </button>
      <button
        type="button"
        data-testid="valkyrie-direct-message"
        title={messageTitle}
        disabled={!huddle || !joined}
        aria-expanded={composing}
        onClick={onCompose}
        className={HERO_BUTTON}
      >
        <Mail size={13} aria-hidden="true" />
        Direct message
      </button>
      <button
        type="button"
        data-testid="valkyrie-change-autonomy"
        aria-expanded={changingAutonomy}
        onClick={onToggleAutonomy}
        className={HERO_BUTTON}
      >
        <Settings2 size={13} aria-hidden="true" />
        Change autonomy
      </button>
      {huddleError ? (
        <p
          role="alert"
          data-testid="valkyrie-huddle-error"
          className="niuu:w-full niuu:text-right niuu:text-xs niuu:text-critical"
        >
          {errorMessage(huddleError, 'Huddle action failed')}
        </p>
      ) : null}
    </div>
  );
}

function DirectMessageComposer({
  valkyrie,
  huddle,
  onClose,
}: {
  valkyrie: ValkyrieResident;
  huddle: HuddleSummary;
  onClose: () => void;
}) {
  const sendMessage = useSendHuddleMessage();
  const [body, setBody] = useState('');

  return (
    <form
      data-testid="valkyrie-dm-composer"
      className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = body.trim();
        if (!trimmed) return;
        sendMessage.mutate(
          {
            huddleId: huddle.id,
            body: trimmed,
            directedTo: [valkyrie.id],
            authorId: OPERATOR_PARTICIPANT_ID,
          },
          { onSuccess: () => setBody('') },
        );
      }}
    >
      <label
        className={`niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] ${MUTED}`}
      >
        direct message to {valkyrie.name}
        <textarea
          aria-label={`Direct message to ${valkyrie.name}`}
          value={body}
          rows={2}
          placeholder={`Delivered to ${valkyrie.name} via ${huddle.title}`}
          onChange={(event) => setBody(event.target.value)}
          className="niuu:mt-2 niuu:w-full niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-2 niuu:font-sans niuu:text-sm niuu:normal-case niuu:tracking-normal niuu:text-text-primary"
        />
      </label>
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="submit"
          data-testid="valkyrie-dm-send"
          disabled={sendMessage.isPending || !body.trim()}
          className={HERO_BUTTON}
        >
          {sendMessage.isPending ? 'Sending…' : 'Send'}
        </button>
        <button type="button" onClick={onClose} className={`niuu:text-xs ${MUTED}`}>
          Close
        </button>
        {sendMessage.isSuccess ? (
          <span data-testid="valkyrie-dm-sent" className="niuu:text-xs niuu:text-state-ok">
            Delivered to {valkyrie.name}.
          </span>
        ) : null}
        {sendMessage.isError ? (
          <span role="alert" className="niuu:text-xs niuu:text-critical">
            {errorMessage(sendMessage.error, 'Sending failed')}
          </span>
        ) : null}
      </div>
    </form>
  );
}

function Hero({
  dashboard,
  valkyrie,
  governance,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
  governance: BuildGovernance;
}) {
  const environment = dashboard.environments.find((entry) => entry.id === valkyrie.environmentId);
  const flock = dashboard.flocks.find(
    (entry) => entry.id === valkyrie.flockId || entry.valkyrieIds.includes(valkyrie.id),
  );
  const updateAutonomy = useUpdateAutonomy();
  const [composing, setComposing] = useState(false);
  const [changingAutonomy, setChangingAutonomy] = useState(false);
  const modeCopy = autonomyModeCopy(valkyrie.autonomyMode);
  const { buildGrant, grantsKnown } = governance;
  const effectiveCopy = autonomyModeCopy(governance.effectiveMode);
  const wakeCopy = wakefulnessCopy(valkyrie.wakefulness);
  const heroLastSeen = valkyrieLastSeenAt(valkyrie);
  const health = environment?.health ?? 'healthy';
  const huddle = openHuddleForEnvironment(dashboard.huddles, valkyrie.environmentId);

  return (
    <section className={`${PANEL} niuu:p-5`} data-testid="valkyrie-console-hero">
      <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-4">
        <div className="niuu:min-w-0">
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <span className="niuu:relative niuu:shrink-0">
              <span className="niuu:flex niuu:h-14 niuu:w-14 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand niuu:bg-brand/10 niuu:text-3xl niuu:text-brand">
                ᛒ
              </span>
              <span className="niuu:absolute niuu:-bottom-1 niuu:-left-1 niuu:flex niuu:h-5 niuu:w-5 niuu:items-center niuu:justify-center niuu:rounded-full niuu:bg-bg-secondary niuu:text-brand">
                {wakefulnessIcon(valkyrie.wakefulness)}
              </span>
            </span>
            <div className="niuu:min-w-0">
              <h1 className="niuu:truncate niuu:font-mono niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
                valkyrie:{valkyrie.name}
              </h1>
              <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">
                {valkyrie.persona} · {valkyrie.specialty}
              </p>
            </div>
          </div>
          <div className="niuu:mt-4 niuu:flex niuu:flex-wrap niuu:gap-2 niuu:text-xs">
            <span
              data-testid="valkyrie-effective-autonomy"
              className="niuu:rounded-full niuu:border niuu:border-brand niuu:bg-brand/10 niuu:px-3 niuu:py-1 niuu:text-brand"
            >
              {effectiveCopy.label}
              {buildGrant
                ? ` · effective (realm grant level ${buildGrant.level})`
                : grantsKnown
                  ? ' · configured (no realm build grant)'
                  : ''}
            </span>
            {buildGrant ? (
              <span
                data-testid="valkyrie-configured-autonomy"
                className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary"
              >
                configured {modeCopy.label.toLowerCase()}
              </span>
            ) : null}
            <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary">
              env {environment?.name ?? valkyrie.environmentId}
            </span>
            {environment ? (
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary">
                {environmentKindLabel(environment.kind)}
              </span>
            ) : null}
            {flock ? (
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary">
                flock {flock.name}
              </span>
            ) : null}
            <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary">
              realm {governance.realm.slug}
            </span>
          </div>
        </div>
        <HeroActions
          valkyrie={valkyrie}
          huddle={huddle}
          composing={composing}
          onCompose={() => setComposing((value) => !value)}
          changingAutonomy={changingAutonomy}
          onToggleAutonomy={() => setChangingAutonomy((value) => !value)}
        />
      </div>
      {changingAutonomy ? (
        <div className="niuu:mt-4 niuu:flex niuu:items-center niuu:gap-3 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3">
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
                  reason: 'Operator change from the Valkyrie console',
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
          </label>
          <p className={`niuu:text-xs ${MUTED}`}>
            Configured mode — the realm build grant still wins for tool builds.
          </p>
        </div>
      ) : null}
      {composing && huddle?.joined ? (
        <DirectMessageComposer
          valkyrie={valkyrie}
          huddle={huddle}
          onClose={() => setComposing(false)}
        />
      ) : null}
      {valkyrie.charter ? (
        <div
          data-testid="valkyrie-charter"
          className="niuu:mt-4 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3"
        >
          <div className="niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
            charter — what this valkyrie is for
          </div>
          <p className="niuu:mt-2 niuu:text-sm niuu:leading-6 niuu:text-text-primary">
            {valkyrie.charter}
          </p>
        </div>
      ) : null}
      <div className="niuu:mt-5 niuu:grid niuu:gap-3 niuu:md:grid-cols-4">
        <Metric
          label="wakefulness"
          value={valkyrie.wakefulness}
          icon={wakefulnessIcon(valkyrie.wakefulness)}
          detail={wakeCopy.description}
        />
        <Metric
          label="operational health"
          value={`${health} · ${valkyrie.status}`}
          valueClassName={healthClasses(health)}
          detail={`judgment confidence ${formatPercent(valkyrie.confidence)}`}
        />
        <Metric
          label="autonomy mode"
          value={effectiveCopy.label.toLowerCase()}
          detail={autonomyModeHint(governance.effectiveMode)}
        />
        <Metric
          label="last seen"
          value={heroLastSeen ? `${timeAgo(heroLastSeen)} ago` : 'unknown'}
          detail={
            environment?.lastSignalAt
              ? `signal ${timeAgo(environment.lastSignalAt)} ago`
              : undefined
          }
        />
      </div>
      <div className="niuu:mt-4 niuu:rounded-md niuu:border niuu:border-brand/60 niuu:bg-brand/10 niuu:p-3">
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-brand">
          <Shield size={13} aria-hidden="true" />
          authority boundary
        </div>
        <p className="niuu:mt-2 niuu:text-sm niuu:text-text-primary">{effectiveCopy.description}</p>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  icon,
  detail,
  valueClassName,
}: {
  label: string;
  value: string;
  icon?: ReactNode;
  detail?: string;
  valueClassName?: string;
}) {
  return (
    <div className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3">
      <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-[11px] niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-muted">
        {icon}
        {label}
      </div>
      <div
        className={`niuu:mt-2 niuu:truncate niuu:text-sm niuu:font-semibold ${
          valueClassName ?? 'niuu:text-text-primary'
        }`}
      >
        {value}
      </div>
      {detail ? <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{detail}</p> : null}
    </div>
  );
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

function SituationPanel({
  decision,
  signalTotal,
  severities,
  environmentId,
}: {
  decision?: DecisionRecord;
  signalTotal: number;
  severities: string[] | undefined;
  environmentId: string;
}) {
  const [browsing, setBrowsing] = useState(false);
  const stateCopy = decision ? operationalStateCopy(decision.operationalState) : null;

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-current-situation">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Sparkles size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Current situation
        </h2>
      </div>
      {decision && stateCopy ? (
        <div className="niuu:mt-5">
          <p className="niuu:text-base niuu:leading-7 niuu:text-text-primary">
            <span className="niuu:font-semibold">{stateCopy.label}</span> — {decision.rationale}
          </p>
          <p className={`niuu:mt-2 niuu:text-xs ${MUTED}`}>
            Decided {timeAgo(decision.decidedAt)} ago with {formatPercent(decision.confidence)}{' '}
            confidence, based on {decision.signalRefs.length} signal(s).{' '}
            {decisionStatusCopy(decision).description}
          </p>
        </div>
      ) : (
        <p className="niuu:mt-5 niuu:text-base niuu:leading-7 niuu:text-text-primary">
          {describeIdleSituation(signalTotal, severities)}
        </p>
      )}
      <button
        type="button"
        onClick={() => setBrowsing((value) => !value)}
        className="niuu:mt-4 niuu:flex niuu:items-center niuu:gap-1 niuu:text-xs niuu:text-brand"
      >
        <ChevronDown
          size={13}
          aria-hidden="true"
          className={browsing ? 'niuu:rotate-180' : undefined}
        />
        {browsing ? 'Hide observed signals' : `Browse observed signals`}
      </button>
      {browsing ? <SignalBrowser environmentId={environmentId} /> : null}
    </section>
  );
}

interface TimelineRow {
  event: ValkyrieEventTelemetry;
  repeats: number;
}

/**
 * Collapse consecutive occurrences of the same routine event (poll ticks,
 * heartbeats) into one row so cadence chatter cannot bury real activity.
 */
function collapseRepeats(events: ValkyrieEventTelemetry[]): TimelineRow[] {
  const rows: TimelineRow[] = [];
  for (const event of events) {
    const last = rows[rows.length - 1];
    if (last && last.event.eventType === event.eventType && last.event.summary === event.summary) {
      last.repeats += 1;
      continue;
    }
    rows.push({ event, repeats: 1 });
  }
  return rows;
}

function Timeline({ events }: { events: ValkyrieEventTelemetry[] }) {
  const [showAll, setShowAll] = useState(false);
  const [kindFilter, setKindFilter] = useState('');
  const kinds = useMemo(() => [...new Set(events.map((event) => event.kind))].sort(), [events]);
  const filtered = useMemo(
    () =>
      collapseRepeats(kindFilter ? events.filter((event) => event.kind === kindFilter) : events),
    [events, kindFilter],
  );
  const visible = showAll ? filtered : filtered.slice(0, TIMELINE_PREVIEW_COUNT);

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-signal-timeline">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Activity size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
            Signal {'->'} action timeline
          </h2>
        </div>
        {kinds.length > 1 ? (
          <select
            aria-label="Filter timeline by kind"
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value)}
            className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-primary"
          >
            <option value="">all kinds</option>
            {kinds.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      <ol className="niuu:mt-5 niuu:flex niuu:flex-col niuu:gap-4">
        {visible.map(({ event, repeats }) => (
          <li key={event.id} className="niuu:grid niuu:grid-cols-[28px_minmax(0,1fr)] niuu:gap-3">
            <span
              className={`niuu:mt-1 niuu:flex niuu:h-7 niuu:w-7 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border ${eventTone(
                event.kind,
              )}`}
            >
              <ChevronRight size={14} aria-hidden="true" />
            </span>
            <div className="niuu:min-w-0">
              <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
                <span className="niuu:truncate niuu:font-mono niuu:text-xs niuu:text-brand">
                  {event.eventType}
                </span>
                <span className="niuu:flex niuu:shrink-0 niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
                  {repeats > 1 ? (
                    <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-[10px]">
                      ×{repeats}
                    </span>
                  ) : null}
                  {timeAgo(event.observedAt)} ago
                </span>
              </div>
              <p className="niuu:mt-1 niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {event.summary}
              </p>
              <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                {event.environmentId}
                {event.valkyrieId ? ` · ${event.valkyrieId}` : ''}
              </p>
            </div>
          </li>
        ))}
      </ol>
      {filtered.length === 0 ? (
        <p className="niuu:mt-4 niuu:text-sm niuu:text-text-muted">No telemetry events yet.</p>
      ) : null}
      {filtered.length > TIMELINE_PREVIEW_COUNT ? (
        <button
          type="button"
          onClick={() => setShowAll((value) => !value)}
          className="niuu:mt-4 niuu:text-xs niuu:text-brand"
        >
          {showAll ? 'Show fewer events' : `Show all ${filtered.length} events`}
        </button>
      ) : null}
    </section>
  );
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
            ? `${effectiveCopy.label} is the effective build autonomy — realm grant level ` +
              `${buildGrant.level} on ${realm.slug}; configured mode is ${modeCopy.label.toLowerCase()}.`
            : `No build grant on realm ${realm.slug} yet — the configured mode ` +
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
          realm build governance — {realm.slug}
        </div>
        {realmExists ? (
          <>
            <p className={`niuu:mt-1 niuu:text-xs ${MUTED}`}>
              The realm&apos;s build grant pins the Ting workflow this resident uses to build tools
              and sets its effective build autonomy.
            </p>
            <ToolBuilderGrantCard realm={realm} />
          </>
        ) : (
          <p
            data-testid="valkyrie-realm-unconfigured"
            className={`niuu:mt-1 niuu:text-xs ${MUTED}`}
          >
            No realm is configured for {realm.slug}, so there is no build governance to set. The
            configured autonomy mode applies until a realm is created.
          </p>
        )}
      </div>
    </section>
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

function PendingReviewsPanel({
  environmentId,
  valkyrieId,
}: {
  environmentId: string;
  valkyrieId: string;
}) {
  const { data } = useReviewList({ status: 'pending' });
  const pending = (data ?? []).filter(
    (item) => item.environmentId === environmentId || item.valkyrieId === valkyrieId,
  );

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-pending-reviews">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Inbox size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
            Waiting on you
          </h2>
        </div>
        <a href="/valkyrie/inbox" className="niuu:text-xs niuu:text-brand">
          Open inbox
        </a>
      </div>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-2">
        {pending.slice(0, 3).map((item) => (
          <a
            key={item.itemId}
            href="/valkyrie/inbox"
            className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                {item.title}
              </span>
              <span className="niuu:shrink-0 niuu:text-[10px] niuu:uppercase niuu:text-text-muted">
                {item.riskClass}
              </span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
              {reviewKindLabel(item.kind)}
            </p>
          </a>
        ))}
        {pending.length > 3 ? (
          <p className={`niuu:text-xs ${MUTED}`}>and {pending.length - 3} more in the inbox.</p>
        ) : null}
        {pending.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>Nothing needs your approval right now.</p>
        ) : null}
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
  const { data: skillStats } = useSkillStats(valkyrie.environmentId);
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
  // The strip is three sources stacked; keep the whole strip to the newest
  // LIST_LIMIT items so a busy environment cannot render an unbounded wall.
  const skills = (skillStats ?? []).slice(
    0,
    Math.max(0, LIST_LIMIT - learnings.length - toolNeeds.length),
  );

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-learning">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Brain size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Learning & tools
        </h2>
      </div>
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
        {skills.map((skill) => (
          <div
            key={skill.skillName}
            data-testid={`skill-stat-${skill.skillName}`}
            className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <span className="niuu:truncate niuu:font-mono niuu:text-sm niuu:text-brand">
                {skill.skillName}
              </span>
              {skill.rolledBackAt ? (
                <span className="niuu:shrink-0 niuu:rounded-full niuu:bg-critical-bg niuu:px-2 niuu:py-0.5 niuu:text-[10px] niuu:text-critical">
                  rolled back
                </span>
              ) : (
                <span className="niuu:shrink-0 niuu:text-xs niuu:text-state-ok">
                  {skill.uses > 0 ? `${Math.round((skill.successes / skill.uses) * 100)}%` : '—'}
                </span>
              )}
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
              Used {skill.uses}× ({skill.successes} ok, {skill.failures} failed) · last{' '}
              {timeAgo(skill.lastUsedAt)} ago —{' '}
              {operationalStateCopy(skill.lastOutcome).label.toLowerCase()}
            </p>
          </div>
        ))}
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
        {skills.length === 0 && toolNeeds.length === 0 && learnings.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>No tool gaps or learning records.</p>
        ) : null}
      </div>
    </section>
  );
}

function LearnedSkillStrip({
  decisions,
  skills,
  onViewSkill,
}: {
  decisions: DecisionRecord[];
  skills: LearnedSkillSummary[];
  onViewSkill: (name: string) => void;
}) {
  const learned = decisions.filter((decision) => isLearnedSkillState(decision.operationalState));
  if (learned.length === 0) return null;
  const skillNames = skills.map((skill) => skill.skillName);
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-learned-activity">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Zap size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Recent learned-skill activity
        </h2>
      </div>
      <div className="niuu:mt-4 niuu:grid niuu:gap-3 niuu:md:grid-cols-2">
        {learned.slice(0, 4).map((decision) => {
          const skillName = referencedSkillName(decision, skillNames);
          return (
            <div key={decision.decisionId} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  {operationalStateCopy(decision.operationalState).label}
                </span>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {timeAgo(decision.decidedAt)} ago
                </span>
              </div>
              <p className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary">
                {decision.rationale}
              </p>
              {skillName ? (
                <p className="niuu:mt-2">
                  <SkillNameButton name={skillName} onOpen={onViewSkill} />
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function LearnedSkillsPanel({
  skills,
  isLoading,
  error,
  onViewSkill,
}: {
  skills: LearnedSkillSummary[];
  isLoading: boolean;
  error: unknown;
  onViewSkill: (name: string) => void;
}) {
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-learned-skills">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <GraduationCap size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
            Learned skills
          </h2>
        </div>
        {skills.length > 0 ? (
          <span className={`niuu:text-xs ${MUTED}`}>{skills.length}</span>
        ) : null}
      </div>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-2">
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
        {skills.map((skill) => (
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
                adopted {timeAgo(skill.adoptedAt)} ago
              </span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{skill.description}</p>
          </button>
        ))}
        {!isLoading && !error && skills.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>
            No learned skills adopted on this environment yet.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function Console({ dashboard }: { dashboard: ValkyrieDashboard }) {
  const [selectedId, setSelectedId] = useState(() => dashboard.valkyries[0]?.id ?? '');
  const [viewedSkillName, setViewedSkillName] = useState<string | null>(null);
  const [viewedLearningId, setViewedLearningId] = useState<string | null>(null);
  const selected =
    dashboard.valkyries.find((entry) => entry.id === selectedId) ?? dashboard.valkyries[0];
  const environmentEvents = useMemo(
    () =>
      [...(dashboard.telemetry?.recentEvents ?? [])]
        .filter((event) => !selected || event.environmentId === selected.environmentId)
        .sort((a, b) => b.observedAt.localeCompare(a.observedAt)),
    [dashboard.telemetry?.recentEvents, selected],
  );
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

  const skills = skillsQuery.data ?? [];
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
  const lastSeen = newestTimestamp([
    selected.lastObservedAt,
    selected.lastActionAt,
    dashboard.telemetry?.lastObservedAt,
  ]);
  const governance: BuildGovernance = {
    realm: { slug: realmSlug, name: environmentSummary?.name ?? realmSlug },
    realmExists,
    buildGrant,
    grantsKnown,
    effectiveMode: buildGrant ? autonomyModeForLevel(buildGrant.level) : selected.autonomyMode,
  };

  return (
    <div className="niuu:grid niuu:h-full niuu:min-h-0 niuu:grid-cols-[320px_minmax(0,1fr)] niuu:bg-bg-primary">
      <Roster
        dashboard={dashboard}
        selectedId={selected.id}
        onSelect={(valkyrieId) => {
          setSelectedId(valkyrieId);
          setViewedSkillName(null);
          setViewedLearningId(null);
        }}
      />
      <main className="niuu:min-h-0 niuu:overflow-auto niuu:p-5">
        <div className="niuu:mb-4 niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:text-xs niuu:text-text-muted">
          <div className="niuu:font-mono">
            Valkyrie / valkyries / <span className="niuu:text-text-primary">{selected.name}</span>
          </div>
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <span>resident {dashboard.valkyries.length}</span>
            <span className="niuu:rounded-full niuu:border niuu:border-brand niuu:bg-brand/10 niuu:px-3 niuu:py-1 niuu:text-brand">
              LIVE
            </span>
            {lastSeen ? <span>{timeAgo(lastSeen)} ago</span> : null}
          </div>
        </div>
        <div className="niuu:flex niuu:flex-col niuu:gap-4">
          <Hero dashboard={dashboard} valkyrie={selected} governance={governance} />
          <div className="niuu:grid niuu:gap-4 niuu:xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)_360px]">
            <SituationPanel
              decision={decisions[0]}
              signalTotal={signalTotal}
              severities={severities}
              environmentId={selected.environmentId}
            />
            <Timeline events={environmentEvents} />
            <AuthorityPanel valkyrie={selected} severities={severities} governance={governance} />
          </div>
          <LearnedSkillStrip
            decisions={decisions}
            skills={skills}
            onViewSkill={setViewedSkillName}
          />
          <div className="niuu:grid niuu:gap-4 niuu:xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="niuu:flex niuu:flex-col niuu:gap-4">
              <section className={PANEL_PAD}>
                <div className="niuu:flex niuu:items-center niuu:gap-2">
                  <AlertTriangle size={15} className="niuu:text-state-warn" aria-hidden="true" />
                  <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
                    Operational state
                  </h2>
                </div>
                <div className="niuu:mt-4 niuu:grid niuu:gap-3 niuu:md:grid-cols-2">
                  {dashboard.operationalStates
                    .filter((state) => state.environmentId === selected.environmentId)
                    .map((state) => (
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
                        <p className="niuu:mt-2 niuu:text-sm niuu:text-text-secondary">
                          {state.observed}
                        </p>
                        <p className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">
                          desired: {state.desired}
                        </p>
                      </div>
                    ))}
                </div>
              </section>
              <DecisionsPanel
                environmentId={selected.environmentId}
                skills={skills}
                onViewSkill={setViewedSkillName}
              />
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-4">
              <PendingReviewsPanel
                environmentId={selected.environmentId}
                valkyrieId={selected.id}
              />
              <LearnedSkillsPanel
                skills={skills}
                isLoading={skillsQuery.isLoading}
                error={skillsQuery.error}
                onViewSkill={setViewedSkillName}
              />
              <LearningPanel
                dashboard={dashboard}
                valkyrie={selected}
                onOpenLearning={setViewedLearningId}
              />
            </div>
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
        message={error instanceof Error ? error.message : 'Unable to load Valkyrie console'}
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
