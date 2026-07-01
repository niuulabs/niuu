import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  Brain,
  ChevronRight,
  GitBranch,
  MessageSquare,
  Moon,
  Radio,
  Shield,
  Sparkles,
  Zap,
} from 'lucide-react';
import type {
  AutonomyMode,
  EnvironmentHealth,
  EnvironmentKind,
  EnvironmentSignal,
  ValkyrieDashboard,
  ValkyrieEventTelemetry,
  ValkyrieResident,
  WakefulnessState,
} from '../domain';
import { useUpdateAutonomy, useValkyrieDashboard } from '../application/useValkyrieDashboard';
import { timeAgo } from './reviewFormat';

const PANEL =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary';
const PANEL_PAD = `${PANEL} niuu:p-4`;
const MUTED = 'niuu:text-text-muted';

const AUTONOMY_MODES: AutonomyMode[] = ['guarded', 'autonomous', 'yolo'];

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
              {valkyrie.autonomyMode}
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
        <p className="niuu:mt-2 niuu:text-sm niuu:text-text-primary">
          Autonomous for telemetry and routine observation. High-risk, destructive, or gated actions
          still require operator review.
        </p>
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

function SituationPanel({
  signal,
  event,
}: {
  signal?: EnvironmentSignal;
  event?: ValkyrieEventTelemetry;
}) {
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-current-situation">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Sparkles size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Current situation
        </h2>
      </div>
      <p className="niuu:mt-5 niuu:text-base niuu:leading-7 niuu:text-text-primary">
        {signal?.summary ??
          event?.summary ??
          'No active situation has been raised by this resident yet.'}
      </p>
      <div className="niuu:mt-5">
        <div className="niuu:mb-2 niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
          latest signal
        </div>
        <div className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
            <span className="niuu:font-mono niuu:text-xs niuu:text-brand">
              {signal?.source ?? event?.eventType ?? 'valkyrie.telemetry'}
            </span>
            <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
              {signal?.severity ?? event?.kind ?? 'event'}
            </span>
          </div>
          <p className="niuu:mt-2 niuu:text-sm niuu:text-text-secondary">
            {signal?.subject ?? event?.source ?? event?.correlationId ?? 'resident observation'}
          </p>
        </div>
      </div>
    </section>
  );
}

function Timeline({ events }: { events: ValkyrieEventTelemetry[] }) {
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-signal-timeline">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Activity size={15} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Signal {'->'} action timeline
        </h2>
      </div>
      <ol className="niuu:mt-5 niuu:flex niuu:flex-col niuu:gap-4">
        {events.slice(0, 7).map((event) => (
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
      {events.length === 0 ? (
        <p className="niuu:mt-4 niuu:text-sm niuu:text-text-muted">No telemetry events yet.</p>
      ) : null}
    </section>
  );
}

function AuthorityPanel({ valkyrie }: { valkyrie: ValkyrieResident }) {
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
            {mode}
          </div>
        ))}
      </div>
      <p className="niuu:mt-4 niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:text-sm niuu:leading-6 niuu:text-text-secondary">
        Acts within its authority boundary. Risky operations and gated decisions route to the review
        inbox.
      </p>
      <div className="niuu:mt-4 niuu:flex niuu:flex-wrap niuu:gap-2">
        {['private allowed', 'environment allowed', 'flock read', 'shared read'].map((scope) => (
          <span
            key={scope}
            className="niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-1 niuu:text-xs niuu:text-text-secondary"
          >
            {scope}
          </span>
        ))}
      </div>
    </section>
  );
}

function SideRail({
  dashboard,
  valkyrie,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
}) {
  const decisions = dashboard.courtDecisions
    .filter((decision) => decision.environmentId === valkyrie.environmentId)
    .slice(0, 4);
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
    <div className="niuu:flex niuu:flex-col niuu:gap-4">
      <section className={PANEL_PAD}>
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <MessageSquare size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
            Recent decisions
          </h2>
        </div>
        <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-3">
          {decisions.map((decision) => (
            <div key={decision.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                  {decision.title}
                </span>
                <span className="niuu:shrink-0 niuu:text-xs niuu:text-text-muted">
                  {decision.status}
                </span>
              </div>
              <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{decision.risk}</p>
            </div>
          ))}
          {decisions.length === 0 ? (
            <p className="niuu:text-sm niuu:text-text-muted">No recent decisions.</p>
          ) : null}
        </div>
      </section>
      <section className={PANEL_PAD}>
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Brain size={15} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
            Learning & tools
          </h2>
        </div>
        <div className="niuu:mt-4 niuu:flex niuu:flex-col niuu:gap-3">
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
          {(toolNeeds?.length ?? 0) === 0 && learnings.length === 0 ? (
            <p className="niuu:text-sm niuu:text-text-muted">No tool gaps or learning records.</p>
          ) : null}
        </div>
      </section>
    </div>
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
      dashboard.signals
        .filter((signal) => !selected || signal.environmentId === selected.environmentId)
        .sort((a, b) => b.receivedAt.localeCompare(a.receivedAt)),
    [dashboard.signals, selected],
  );

  if (!selected) return <EmptyConsole />;

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
            <SituationPanel signal={environmentSignals[0]} event={environmentEvents[0]} />
            <Timeline events={environmentEvents} />
            <AuthorityPanel valkyrie={selected} />
          </div>
          <div className="niuu:grid niuu:gap-4 niuu:xl:grid-cols-[minmax(0,1fr)_360px]">
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
                        <span className="niuu:text-xs niuu:text-text-muted">{state.drift}</span>
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
            <SideRail dashboard={dashboard} valkyrie={selected} />
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
