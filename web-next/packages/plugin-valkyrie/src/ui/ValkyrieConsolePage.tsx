import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  Brain,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Inbox,
  MessageSquare,
  Moon,
  Radio,
  Shield,
  Sparkles,
  Zap,
} from 'lucide-react';
import type {
  AutonomyMode,
  DecisionRecord,
  EnvironmentHealth,
  EnvironmentKind,
  ValkyrieDashboard,
  ValkyrieEventTelemetry,
  ValkyrieResident,
  WakefulnessState,
} from '../domain';
import { useUpdateAutonomy, useValkyrieDashboard } from '../application/useValkyrieDashboard';
import {
  useDecisionDetail,
  useDecisionList,
  useSignalHistory,
  useSkillStats,
} from '../application/useValkyrieHistory';
import { useReviewList } from '../application/useReviews';
import { timeAgo } from './reviewFormat';
import {
  actionAuthorityCopy,
  autonomyModeCopy,
  describeIdleSituation,
  isLearnedSkillState,
  operationalStateCopy,
  outcomeCopy,
  reviewKindLabel,
  severityCopy,
} from './copy';

const PANEL =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary';
const PANEL_PAD = `${PANEL} niuu:p-4`;
const MUTED = 'niuu:text-text-muted';

const AUTONOMY_MODES: AutonomyMode[] = ['guarded', 'autonomous', 'yolo'];
const TIMELINE_PREVIEW_COUNT = 7;
const SIGNAL_PAGE_SIZE = 10;

function healthClasses(health: EnvironmentHealth | ValkyrieResident['status']): string {
  if (health === 'critical' || health === 'blocked') return 'niuu:text-critical';
  if (health === 'degraded' || health === 'busy') return 'niuu:text-state-warn';
  if (health === 'watch' || health === 'online') return 'niuu:text-brand';
  return 'niuu:text-text-muted';
}

function healthDotClasses(health: EnvironmentHealth | ValkyrieResident['status']): string {
  if (health === 'critical' || health === 'blocked') return 'niuu:bg-critical';
  if (health === 'degraded' || health === 'busy') return 'niuu:bg-state-warn';
  if (health === 'watch' || health === 'online') return 'niuu:bg-brand';
  return 'niuu:bg-text-muted';
}

function kindIcon(kind: EnvironmentKind) {
  if (kind === 'kubernetes') return <GitBranch size={14} aria-hidden="true" />;
  if (kind === 'host') return <Activity size={14} aria-hidden="true" />;
  if (kind === 'printer') return <Radio size={14} aria-hidden="true" />;
  return <Shield size={14} aria-hidden="true" />;
}

function wakefulnessIcon(state: WakefulnessState) {
  if (state === 'dreaming') return <Moon size={13} aria-hidden="true" />;
  if (state === 'wakeful') return <Zap size={13} aria-hidden="true" />;
  return <Radio size={13} aria-hidden="true" />;
}

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

function groupByEnvironment(dashboard: ValkyrieDashboard) {
  return dashboard.environments.map((environment) => ({
    environment,
    valkyries: dashboard.valkyries.filter((valkyrie) => valkyrie.environmentId === environment.id),
  }));
}

function Roster({
  dashboard,
  selectedId,
  onSelect,
}: {
  dashboard: ValkyrieDashboard;
  selectedId: string;
  onSelect: (valkyrieId: string) => void;
}) {
  return (
    <aside
      data-testid="valkyrie-roster"
      className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:border-r niuu:border-border niuu:bg-bg-secondary"
    >
      <div className="niuu:border-b niuu:border-border niuu:p-4">
        <div className="niuu:flex niuu:items-center niuu:justify-between">
          <h2 className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.16em] niuu:text-text-muted">
            Roster
          </h2>
          <span className="niuu:text-xs niuu:text-text-muted">{dashboard.valkyries.length}</span>
        </div>
      </div>
      <div className="niuu:min-h-0 niuu:flex-1 niuu:overflow-auto niuu:p-3">
        {groupByEnvironment(dashboard).map(({ environment, valkyries }) => (
          <section key={environment.id} className="niuu:mb-4">
            <div className="niuu:mb-2 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2 niuu:px-1">
              <div
                className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] ${healthClasses(
                  environment.health,
                )}`}
              >
                {kindIcon(environment.kind)}
                <span className="niuu:truncate">{environment.name}</span>
              </div>
              <span className="niuu:text-[11px] niuu:text-text-muted">{valkyries.length}</span>
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-2">
              {valkyries.map((valkyrie) => {
                const selected = selectedId === valkyrie.id;
                return (
                  <button
                    key={valkyrie.id}
                    type="button"
                    onClick={() => onSelect(valkyrie.id)}
                    aria-pressed={selected}
                    className={`niuu:flex niuu:w-full niuu:items-center niuu:gap-3 niuu:rounded-md niuu:border niuu:border-solid niuu:p-3 niuu:text-left ${
                      selected
                        ? 'niuu:border-brand niuu:bg-brand/12'
                        : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
                    }`}
                  >
                    <span
                      className={`niuu:flex niuu:h-8 niuu:w-8 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-border ${healthClasses(
                        valkyrie.status,
                      )}`}
                    >
                      ᛒ
                    </span>
                    <span className="niuu:min-w-0 niuu:flex-1">
                      <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
                        {valkyrie.name}
                      </span>
                      <span className="niuu:block niuu:truncate niuu:text-xs niuu:text-text-muted">
                        {valkyrie.wakefulness} · {valkyrie.status}
                      </span>
                    </span>
                    <span className="niuu:flex niuu:flex-col niuu:items-end niuu:gap-1">
                      <span
                        className={`niuu:h-2 niuu:w-2 niuu:rounded-full ${healthDotClasses(
                          valkyrie.status,
                        )}`}
                      />
                      <span className="niuu:text-[10px] niuu:text-text-muted">
                        {valkyrie.lastObservedAt ? `${timeAgo(valkyrie.lastObservedAt)} ago` : ''}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </aside>
  );
}

function Hero({
  dashboard,
  valkyrie,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
}) {
  const environment = dashboard.environments.find((entry) => entry.id === valkyrie.environmentId);
  const flock = dashboard.flocks.find(
    (entry) => entry.id === valkyrie.flockId || entry.valkyrieIds.includes(valkyrie.id),
  );
  const updateAutonomy = useUpdateAutonomy();
  const modeCopy = autonomyModeCopy(valkyrie.autonomyMode);

  return (
    <section className={`${PANEL} niuu:p-5`} data-testid="valkyrie-console-hero">
      <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-4">
        <div className="niuu:min-w-0">
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <span className="niuu:flex niuu:h-14 niuu:w-14 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-brand niuu:bg-brand/10 niuu:text-3xl niuu:text-brand">
              ᛒ
            </span>
            <div className="niuu:min-w-0">
              <h1 className="niuu:truncate niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
                valkyrie:{valkyrie.name}
              </h1>
              <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">
                {valkyrie.persona} · {valkyrie.specialty}
              </p>
            </div>
          </div>
          <div className="niuu:mt-4 niuu:flex niuu:flex-wrap niuu:gap-2 niuu:text-xs">
            <span className="niuu:rounded-full niuu:border niuu:border-brand niuu:bg-brand/10 niuu:px-3 niuu:py-1 niuu:text-brand">
              {modeCopy.label}
            </span>
            <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary">
              env {environment?.name ?? valkyrie.environmentId}
            </span>
            {flock ? (
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-secondary">
                flock {flock.name}
              </span>
            ) : null}
          </div>
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
                reason: 'Operator change from the Valkyrie console',
                participantId: 'human:operator',
              })
            }
            className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
          >
            {AUTONOMY_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>
      </div>
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
        />
        <Metric label="health" value={environment?.health ?? valkyrie.status} />
        <Metric label="confidence" value={formatPercent(valkyrie.confidence)} />
        <Metric
          label="last seen"
          value={valkyrie.lastObservedAt ? `${timeAgo(valkyrie.lastObservedAt)} ago` : 'unknown'}
        />
      </div>
      <div className="niuu:mt-4 niuu:rounded-md niuu:border niuu:border-brand/60 niuu:bg-brand/10 niuu:p-3">
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-brand">
          <Shield size={13} aria-hidden="true" />
          authority boundary
        </div>
        <p className="niuu:mt-2 niuu:text-sm niuu:text-text-primary">{modeCopy.description}</p>
      </div>
    </section>
  );
}

function Metric({ label, value, icon }: { label: string; value: string; icon?: ReactNode }) {
  return (
    <div className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3">
      <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-[11px] niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-muted">
        {icon}
        {label}
      </div>
      <div className="niuu:mt-2 niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
        {value}
      </div>
    </div>
  );
}

function SignalBrowser({ environmentId }: { environmentId: string }) {
  const [severity, setSeverity] = useState('');
  const [offset, setOffset] = useState(0);
  const { data, isLoading } = useSignalHistory({
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
            {actionAuthorityCopy(decision.actionAuthority).description}
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

function Timeline({ events }: { events: ValkyrieEventTelemetry[] }) {
  const [showAll, setShowAll] = useState(false);
  const [kindFilter, setKindFilter] = useState('');
  const kinds = useMemo(() => [...new Set(events.map((event) => event.kind))].sort(), [events]);
  const filtered = kindFilter ? events.filter((event) => event.kind === kindFilter) : events;
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
        {visible.map((event) => (
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
                <span className="niuu:shrink-0 niuu:text-xs niuu:text-text-muted">
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
}: {
  valkyrie: ValkyrieResident;
  severities: string[] | undefined;
}) {
  const modeCopy = autonomyModeCopy(valkyrie.autonomyMode);
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
              mode === valkyrie.autonomyMode
                ? 'niuu:bg-brand/25 niuu:text-text-primary'
                : 'niuu:bg-bg-primary niuu:text-text-muted'
            }`}
          >
            {autonomyModeCopy(mode).label}
          </div>
        ))}
      </div>
      <p className="niuu:mt-4 niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:text-sm niuu:leading-6 niuu:text-text-secondary">
        {modeCopy.description}
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
    </section>
  );
}

function DecisionCard({
  decision,
  expanded,
  onToggle,
}: {
  decision: DecisionRecord;
  expanded: boolean;
  onToggle: () => void;
}) {
  const stateCopy = operationalStateCopy(decision.operationalState);
  const authority = actionAuthorityCopy(decision.actionAuthority);
  const outcome = outcomeCopy(decision.outcome);
  const detail = useDecisionDetail(expanded ? decision.decisionId : null);

  return (
    <div className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="niuu:flex niuu:w-full niuu:items-start niuu:justify-between niuu:gap-3 niuu:text-left"
      >
        <span className="niuu:min-w-0">
          <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
            {stateCopy.label}
            {decision.recommendedAction && decision.recommendedAction !== 'none'
              ? ` · ${decision.recommendedAction.replace(/_/g, ' ')}`
              : ''}
          </span>
          <span className="niuu:mt-1 niuu:block niuu:text-xs niuu:text-text-muted">
            {timeAgo(decision.decidedAt)} ago · {formatPercent(decision.confidence)} confidence ·{' '}
            {authority.label}
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
          ) : null}
          {!decision.outcome && decision.reviewItemId ? (
            <span className="niuu:rounded-full niuu:bg-brand/15 niuu:px-2 niuu:py-0.5 niuu:text-brand">
              awaiting review
            </span>
          ) : null}
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

function DecisionsPanel({ environmentId }: { environmentId: string }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const { data, isLoading } = useDecisionList({ environmentId, limit: 8 });

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-decisions">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <MessageSquare size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">Decisions</h2>
        </div>
        {data ? <span className={`niuu:text-xs ${MUTED}`}>{data.total} total</span> : null}
      </div>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-3">
        {isLoading ? <p className={`niuu:text-sm ${MUTED}`}>Loading decisions...</p> : null}
        {(data?.items ?? []).map((decision) => (
          <DecisionCard
            key={decision.decisionId}
            decision={decision}
            expanded={expandedId === decision.decisionId}
            onToggle={() =>
              setExpandedId(expandedId === decision.decisionId ? null : decision.decisionId)
            }
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

function LearningPanel({
  dashboard,
  valkyrie,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
}) {
  const { data: skills } = useSkillStats(valkyrie.environmentId);
  const learnings = dashboard.learnings
    .filter(
      (learning) =>
        learning.sourceEnvironmentId === valkyrie.environmentId ||
        learning.sourceValkyrieId === valkyrie.id,
    )
    .slice(0, 4);
  const toolNeeds = dashboard.telemetry?.recentToolNeeds
    ?.filter((need) => need.environmentId === valkyrie.environmentId)
    .slice(0, 4);

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-learning">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Brain size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Learning & tools
        </h2>
      </div>
      <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-3">
        {(skills ?? []).map((skill) => (
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
        {(toolNeeds ?? []).map((need) => (
          <div
            key={need.id}
            className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:font-mono niuu:text-sm niuu:text-brand">{need.capability}</div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary">{need.summary}</p>
          </div>
        ))}
        {learnings.map((learning) => (
          <div key={learning.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                {learning.title}
              </span>
              <span className="niuu:text-xs niuu:text-brand">{learning.status}</span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{learning.summary}</p>
          </div>
        ))}
        {(skills?.length ?? 0) === 0 && (toolNeeds?.length ?? 0) === 0 && learnings.length === 0 ? (
          <p className={`niuu:text-sm ${MUTED}`}>No tool gaps or learning records.</p>
        ) : null}
      </div>
    </section>
  );
}

function LearnedSkillStrip({ decisions }: { decisions: DecisionRecord[] }) {
  const learned = decisions.filter((decision) => isLearnedSkillState(decision.operationalState));
  if (learned.length === 0) return null;
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-learned-activity">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Zap size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Recent learned-skill activity
        </h2>
      </div>
      <div className="niuu:mt-4 niuu:grid niuu:gap-3 niuu:md:grid-cols-2">
        {learned.slice(0, 4).map((decision) => (
          <div key={decision.decisionId} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {operationalStateCopy(decision.operationalState).label}
              </span>
              <span className="niuu:text-xs niuu:text-text-muted">
                {timeAgo(decision.decidedAt)} ago
              </span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-secondary">{decision.rationale}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Console({ dashboard }: { dashboard: ValkyrieDashboard }) {
  const [selectedId, setSelectedId] = useState(() => dashboard.valkyries[0]?.id ?? '');
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
    { environmentId: selected?.environmentId ?? '', limit: 8 },
    Boolean(selected),
  );

  if (!selected) return <EmptyConsole />;

  const severities = taskSeverities(selected, dashboard);
  const decisions = decisionsQuery.data?.items ?? [];
  const environmentSummary = dashboard.environments.find(
    (entry) => entry.id === selected.environmentId,
  );
  const signalTotal = environmentSummary?.signalCount ?? environmentSignals.length;
  const lastSeen = newestTimestamp([
    selected.lastObservedAt,
    selected.lastActionAt,
    dashboard.telemetry?.lastObservedAt,
  ]);

  return (
    <div className="niuu:grid niuu:h-full niuu:min-h-0 niuu:grid-cols-[320px_minmax(0,1fr)] niuu:bg-bg-primary">
      <Roster dashboard={dashboard} selectedId={selected.id} onSelect={setSelectedId} />
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
          <Hero dashboard={dashboard} valkyrie={selected} />
          <div className="niuu:grid niuu:gap-4 niuu:xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)_360px]">
            <SituationPanel
              decision={decisions[0]}
              signalTotal={signalTotal}
              severities={severities}
              environmentId={selected.environmentId}
            />
            <Timeline events={environmentEvents} />
            <AuthorityPanel valkyrie={selected} severities={severities} />
          </div>
          <LearnedSkillStrip decisions={decisions} />
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
              <DecisionsPanel environmentId={selected.environmentId} />
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-4">
              <PendingReviewsPanel
                environmentId={selected.environmentId}
                valkyrieId={selected.id}
              />
              <LearningPanel dashboard={dashboard} valkyrie={selected} />
            </div>
          </div>
        </div>
      </main>
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
