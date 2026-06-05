import { useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bell,
  Brain,
  Check,
  ChevronLeft,
  Cpu,
  Database,
  GitBranch,
  ListChecks,
  MessageSquare,
  Moon,
  Radio,
  RotateCcw,
  Shield,
  Terminal,
  Users,
  Wrench,
  X,
  Zap,
} from 'lucide-react';
import type {
  AutonomyMode,
  EnvironmentKind,
  LearningRecord,
  ValkyrieDashboard,
  ValkyrieTelemetry,
} from '../domain';
import { selectEnvironmentSlice, selectFlockLearnings } from '../application/selectors';
import {
  useValkyrieActions,
  useValkyrieDashboard,
  useValkyrieSignals,
} from '../application/useValkyrieDashboard';

const PANEL =
  'niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:rounded-md';
const PANEL_PAD = `${PANEL} niuu:p-3`;
const MUTED = 'niuu:text-text-muted';
const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:justify-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:hover:border-brand niuu:disabled:opacity-50';
const ICON_BUTTON =
  'niuu:inline-flex niuu:h-8 niuu:w-8 niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand';

function kindLabel(kind: EnvironmentKind): string {
  switch (kind) {
    case 'kubernetes':
      return 'k8s';
    case 'host':
      return 'host';
    case 'printer':
      return 'printer';
    default:
      return 'generic';
  }
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: 'compact' }).format(value);
}

function valueOrNone(value: string | undefined): string {
  return value?.trim() ? value : 'none';
}

function detailText(details: Record<string, unknown> | undefined): string {
  if (!details) return '';
  const entries = Object.entries(details).filter(([, value]) => value !== '' && value !== undefined);
  if (entries.length === 0) return '';
  return entries
    .slice(0, 5)
    .map(([key, value]) => {
      if (Array.isArray(value)) return `${key}: ${value.length}`;
      if (typeof value === 'object' && value !== null) return `${key}: {...}`;
      return `${key}: ${String(value)}`;
    })
    .join(' · ');
}

function environmentName(dashboard: ValkyrieDashboard, environmentId: string): string {
  const seeded = dashboard.environments.find((environment) => environment.id === environmentId);
  return seeded?.name ?? environmentId;
}

function formatShortTime(value: string | undefined): string {
  if (!value) return 'none';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function environmentIcon(kind: EnvironmentKind) {
  if (kind === 'kubernetes') return <GitBranch size={16} aria-hidden="true" />;
  if (kind === 'host') return <Activity size={16} aria-hidden="true" />;
  if (kind === 'printer') return <Radio size={16} aria-hidden="true" />;
  return <Shield size={16} aria-hidden="true" />;
}

function EmptyState({ label }: { label: string }) {
  return (
    <div
      data-testid="valkyrie-empty-state"
      className="niuu:flex niuu:min-h-24 niuu:items-center niuu:justify-center niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border niuu:text-sm niuu:text-text-muted"
    >
      {label}
    </div>
  );
}

function LoadingState() {
  return (
    <div
      data-testid="valkyrie-loading-state"
      className="niuu:flex niuu:h-full niuu:min-h-96 niuu:items-center niuu:justify-center niuu:text-sm niuu:text-text-muted"
    >
      Loading Valkyries
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div
      role="alert"
      data-testid="valkyrie-error-state"
      className="niuu:m-4 niuu:rounded-lg niuu:border niuu:border-solid niuu:border-critical niuu:bg-critical-bg niuu:p-4 niuu:text-sm niuu:text-critical"
    >
      {message}
    </div>
  );
}

interface EnvironmentRailProps {
  dashboard: ValkyrieDashboard;
  selectedEnvironmentId: string;
  onSelectEnvironment: (environmentId: string) => void;
  selectedFlockId: string | null;
  onSelectFlock: (flockId: string) => void;
}

function EnvironmentRail({
  dashboard,
  selectedEnvironmentId,
  onSelectEnvironment,
  selectedFlockId,
  onSelectFlock,
}: EnvironmentRailProps) {
  return (
    <aside className="niuu:flex niuu:h-full niuu:min-h-0 niuu:min-w-0 niuu:flex-col niuu:gap-3 niuu:overflow-auto">
      <section className={PANEL_PAD} aria-label="Environments">
        <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between">
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Environments</h2>
          <span className={MUTED}>{dashboard.environments.length}</span>
        </div>
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          {dashboard.environments.map((environment) => {
            const selected = environment.id === selectedEnvironmentId;
            return (
              <button
                key={environment.id}
                type="button"
                data-testid={`environment-${environment.id}`}
                aria-pressed={selected}
                onClick={() => onSelectEnvironment(environment.id)}
                className={`niuu:flex niuu:w-full niuu:items-start niuu:gap-3 niuu:rounded-md niuu:border niuu:border-solid niuu:p-3 niuu:text-left ${
                  selected
                    ? 'niuu:border-brand niuu:bg-brand/12'
                    : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
                }`}
              >
                <span className="niuu:mt-0.5 niuu:text-brand">
                  {environmentIcon(environment.kind)}
                </span>
                <span className="niuu:min-w-0 niuu:flex-1">
                  <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
                    {environment.name}
                  </span>
                  <span className="niuu:block niuu:text-xs niuu:text-text-muted">
                    {kindLabel(environment.kind)} · {environment.health}
                  </span>
                </span>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {environment.unresolvedSignalCount}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className={PANEL_PAD} aria-label="Flocks">
        <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between">
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Flocks</h2>
          <Users size={16} className="niuu:text-brand" aria-hidden="true" />
        </div>
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          {dashboard.flocks.map((flock) => (
            <button
              key={flock.id}
              type="button"
              data-testid={`flock-${flock.id}`}
              aria-pressed={selectedFlockId === flock.id}
              onClick={() => onSelectFlock(flock.id)}
              className={`niuu:rounded-md niuu:border niuu:border-solid niuu:p-3 niuu:text-left ${
                selectedFlockId === flock.id
                  ? 'niuu:border-brand niuu:bg-brand/12'
                  : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
              }`}
            >
              <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {flock.name}
              </span>
              <span className="niuu:block niuu:text-xs niuu:text-text-muted">
                {flock.natsSubject}
              </span>
            </button>
          ))}
        </div>
      </section>
    </aside>
  );
}

function KpiStrip({ dashboard }: { dashboard: ValkyrieDashboard }) {
  const unresolved = dashboard.signals.filter((signal) => signal.status !== 'resolved').length;
  const dreaming = dashboard.valkyries.filter(
    (valkyrie) => valkyrie.wakefulness === 'dreaming',
  ).length;
  const flockLearnings = dashboard.learnings.filter(
    (learning) => learning.scope === 'flock',
  ).length;
  const yoloCount = dashboard.valkyries.filter(
    (valkyrie) => valkyrie.autonomyMode === 'yolo',
  ).length;
  const items = [
    { label: 'Signals', value: unresolved, icon: Bell },
    { label: 'Residents', value: dashboard.valkyries.length, icon: Shield },
    { label: 'Dreaming', value: dreaming, icon: Moon },
    { label: 'Flock learning', value: flockLearnings, icon: Brain },
    { label: 'YOLO', value: yoloCount, icon: RotateCcw },
  ];
  return (
    <section
      data-testid="valkyrie-kpi-strip"
      className="niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:md:grid-cols-5"
    >
      {items.map((item) => (
        <div key={item.label} className={PANEL_PAD}>
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:text-text-muted">
            <span className="niuu:text-xs">{item.label}</span>
            <item.icon size={16} aria-hidden="true" />
          </div>
          <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {item.value}
          </div>
        </div>
      ))}
    </section>
  );
}

function TelemetryPanel({ telemetry }: { telemetry?: ValkyrieTelemetry }) {
  if (!telemetry) return null;
  const totals = telemetry.totals;
  const signalYield =
    totals.signalsCollected > 0 ? totals.signalsPublished / totals.signalsCollected : 0;
  const recentOutcomes = telemetry.recentOutcomes ?? [];
  const activeTasks = Math.max(0, totals.tasksStarted - totals.tasksCompleted - totals.tasksFailed);
  const liveEnvironments = telemetry.byEnvironment.filter(
    (environment) =>
      environment.pollsCompleted +
        environment.pollFailures +
        environment.tasksStarted +
        environment.tasksCompleted +
        environment.tasksDropped +
        environment.judgments +
        environment.actions >
      0,
  );
  const taskState =
    activeTasks > 0
      ? `${compactNumber(activeTasks)} active`
      : totals.tasksDropped > 0
        ? 'budget or queue pressure'
        : totals.tasksCompleted > 0
          ? 'completed work'
          : 'watching';
  const items = [
    { label: 'Events', value: compactNumber(totals.eventsObserved) },
    { label: 'Signals in', value: compactNumber(totals.signalsCollected) },
    { label: 'Published', value: compactNumber(totals.signalsPublished) },
    { label: 'Tasks', value: compactNumber(totals.tasksEnqueued) },
    { label: 'Started', value: compactNumber(totals.tasksStarted) },
    { label: 'Done', value: compactNumber(totals.tasksCompleted) },
    { label: 'Dropped', value: compactNumber(totals.tasksDropped) },
    { label: 'Judgments', value: compactNumber(totals.judgments) },
    { label: 'Actions', value: compactNumber(totals.actions) },
    { label: 'Learning', value: compactNumber(totals.learningEvents) },
    { label: 'Dreams', value: `${totals.dreamCyclesCompleted}/${totals.dreamCyclesStarted}` },
  ];

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-telemetry-panel">
      <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-3">
        <div className="niuu:min-w-0">
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <Activity size={16} className="niuu:text-brand" aria-hidden="true" />
            <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              Operational telemetry
            </h2>
            <span
              className={`niuu:rounded-full niuu:px-2 niuu:py-1 niuu:text-xs ${
                telemetry.verified
                  ? 'niuu:bg-success-bg niuu:text-success'
                  : 'niuu:bg-warning-bg niuu:text-warning'
              }`}
            >
              {telemetry.verified ? 'verified' : 'unverified'}
            </span>
          </div>
          <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
            {telemetry.source} · observed {formatShortTime(telemetry.lastObservedAt)}
          </p>
        </div>
        <div className="niuu:min-w-44 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs">
          <div className={MUTED}>LLM</div>
          <div className="niuu:mt-1 niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
            {telemetry.llm.model || 'unknown'}
          </div>
          <div className="niuu:mt-1 niuu:text-text-muted">
            {telemetry.llm.status} · reflection{' '}
            {telemetry.llm.postSessionReflectionEnabled ? 'on' : 'off'}
          </div>
        </div>
      </div>

      <div
        className="niuu:mt-3 niuu:grid niuu:gap-2 niuu:md:grid-cols-2 niuu:xl:grid-cols-4"
        data-testid="live-k8s-valkyries"
      >
        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className={MUTED}>Live environments</div>
          <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {compactNumber(liveEnvironments.length)}
          </div>
          <div className="niuu:mt-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
            {liveEnvironments.map((environment) => environment.environmentId).join(', ') || 'none'}
          </div>
        </div>
        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className={MUTED}>Thinking</div>
          <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {taskState}
          </div>
          <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
            {compactNumber(totals.tasksStarted)} started · {compactNumber(totals.tasksCompleted)}{' '}
            completed
          </div>
        </div>
        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className={MUTED}>Conclusions</div>
          <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {compactNumber(totals.judgments + totals.actions)}
          </div>
          <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
            {compactNumber(totals.judgments)} judgments · {compactNumber(totals.actions)} actions
          </div>
        </div>
        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className={MUTED}>Dream cycles</div>
          <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {compactNumber(totals.dreamCyclesCompleted)}
          </div>
          <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
            {compactNumber(totals.learningEvents)} learning events
          </div>
        </div>
      </div>

      <div className="niuu:mt-3 niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:md:grid-cols-4 niuu:2xl:grid-cols-11">
        {items.map((item) => (
          <div
            key={item.label}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2"
          >
            <div className="niuu:text-xs niuu:text-text-muted">{item.label}</div>
            <div className="niuu:mt-1 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
              {item.value}
            </div>
          </div>
        ))}
      </div>

      <div className="niuu:mt-3 niuu:grid niuu:gap-3 niuu:xl:grid-cols-[1fr_1fr] niuu:2xl:grid-cols-[1fr_1fr_0.8fr]">
        <div
          className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:xl:col-span-2"
          data-testid="valkyrie-live-conclusions"
        >
          <div className="niuu:mb-2 niuu:flex niuu:items-center niuu:justify-between">
            <h3 className="niuu:text-xs niuu:font-semibold niuu:text-text-muted">
              Live conclusions
            </h3>
            <span className="niuu:text-xs niuu:text-text-muted">
              {compactNumber(recentOutcomes.length)} observed
            </span>
          </div>
          <div className="niuu:grid niuu:gap-2">
            {recentOutcomes.slice(0, 5).map((outcome) => (
              <div
                key={`${outcome.eventType}:${outcome.taskId}:${outcome.observedAt}`}
                className="niuu:grid niuu:gap-2 niuu:rounded-md niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:md:grid-cols-[minmax(0,1fr)_auto]"
              >
                <div className="niuu:min-w-0">
                  <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
                    <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                      {outcome.environmentId}
                    </span>
                    <span className="niuu:rounded-full niuu:bg-bg-primary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                      {outcome.type}
                    </span>
                    <span className="niuu:rounded-full niuu:bg-bg-primary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                      {outcome.verdict || outcome.tier || 'pending'}
                    </span>
                    {typeof outcome.confidence === 'number' ? (
                      <span className="niuu:text-xs niuu:text-text-muted">
                        {percent(outcome.confidence)}
                      </span>
                    ) : null}
                  </div>
                  <div className="niuu:mt-1 niuu:text-sm niuu:text-text-primary">
                    {outcome.summary || outcome.recommendedAction || outcome.taskId}
                  </div>
                  <div className="niuu:mt-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
                    {outcome.recommendedAction || outcome.eventType} · {outcome.taskId}
                  </div>
                </div>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {formatShortTime(outcome.observedAt)}
                </span>
              </div>
            ))}
            {recentOutcomes.length === 0 ? <EmptyState label="No conclusions observed" /> : null}
          </div>
        </div>

        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className="niuu:mb-2 niuu:flex niuu:items-center niuu:justify-between">
            <h3 className="niuu:text-xs niuu:font-semibold niuu:text-text-muted">Recent polls</h3>
            <span className="niuu:text-xs niuu:text-text-muted">yield {percent(signalYield)}</span>
          </div>
          <div className="niuu:grid niuu:gap-2">
            {telemetry.recentPolls.slice(0, 5).map((poll) => (
              <div
                key={`${poll.environmentId}:${poll.sourceId}:${poll.observedAt}`}
                className="niuu:grid niuu:grid-cols-[minmax(0,1fr)_auto] niuu:items-center niuu:gap-2 niuu:rounded-md niuu:bg-bg-secondary niuu:px-3 niuu:py-2"
              >
                <div className="niuu:min-w-0">
                  <div className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                    {poll.environmentId} · {poll.sourceId || 'source'}
                  </div>
                  <div className="niuu:truncate niuu:text-xs niuu:text-text-muted">
                    {poll.status === 'failed'
                      ? poll.error || 'failed'
                      : `${poll.collected ?? 0} collected · ${poll.published ?? 0} published · ${
                          poll.tasksEnqueued ?? 0
                        } tasks`}
                  </div>
                </div>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {formatShortTime(poll.observedAt)}
                </span>
              </div>
            ))}
            {telemetry.recentPolls.length === 0 ? <EmptyState label="No poll telemetry" /> : null}
          </div>
        </div>

        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <div className="niuu:mb-2 niuu:flex niuu:items-center niuu:justify-between">
            <h3 className="niuu:text-xs niuu:font-semibold niuu:text-text-muted">Recent tasks</h3>
            <span className="niuu:text-xs niuu:text-text-muted">
              failed {compactNumber(totals.tasksFailed)}
            </span>
          </div>
          <div className="niuu:grid niuu:gap-2">
            {(telemetry.recentTasks ?? []).slice(0, 5).map((task) => (
              <div
                key={`${task.taskId}:${task.status}:${task.observedAt}`}
                className="niuu:grid niuu:grid-cols-[minmax(0,1fr)_auto] niuu:items-center niuu:gap-2 niuu:rounded-md niuu:bg-bg-secondary niuu:px-3 niuu:py-2"
              >
                <div className="niuu:min-w-0">
                  <div className="niuu:truncate niuu:text-sm niuu:text-text-primary">
                    {task.title || task.taskId}
                  </div>
                  <div className="niuu:truncate niuu:text-xs niuu:text-text-muted">
                    {task.environmentId} · {task.status}
                    {task.reason ? ` · ${task.reason}` : ''}
                    {task.outcome ? ` · ${task.outcome}` : ''}
                  </div>
                </div>
                <span className="niuu:text-xs niuu:text-text-muted">
                  {formatShortTime(task.observedAt)}
                </span>
              </div>
            ))}
            {(telemetry.recentTasks ?? []).length === 0 ? (
              <EmptyState label="No task telemetry" />
            ) : null}
          </div>
        </div>

        <div className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3">
          <h3 className="niuu:mb-2 niuu:text-xs niuu:font-semibold niuu:text-text-muted">Gaps</h3>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            {telemetry.gaps.slice(0, 5).map((gap) => (
              <div
                key={gap}
                className="niuu:rounded-md niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-muted"
              >
                {gap}
              </div>
            ))}
            {telemetry.gaps.length === 0 ? (
              <div className="niuu:rounded-md niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-muted">
                No telemetry gaps in the current window
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function LiveEnvironmentMatrix({ telemetry }: { telemetry: ValkyrieTelemetry }) {
  const environments = telemetry.byEnvironment.filter(
    (environment) =>
      environment.pollsCompleted +
        environment.pollFailures +
        environment.signalsCollected +
        environment.tasksEnqueued +
        environment.tasksStarted +
        environment.tasksCompleted +
        environment.tasksDropped +
        environment.judgments +
        environment.actions +
        environment.learningEvents +
        environment.dreamCycles >
      0,
  );

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-live-environments">
      <div className="niuu:mb-3 niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Environments</h2>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(environments.length)} observed
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2 niuu:xl:grid-cols-2">
        {environments.map((environment) => (
          <article
            key={environment.environmentId}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-2">
              <div className="niuu:min-w-0">
                <h3 className="niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {environment.environmentId}
                </h3>
                <p className="niuu:text-xs niuu:text-text-muted">
                  seen {formatShortTime(environment.lastObservedAt)}
                </p>
              </div>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                {compactNumber(environment.judgments + environment.actions)} conclusions
              </span>
            </div>
            <dl className="niuu:mt-3 niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:text-xs niuu:md:grid-cols-4">
              <div>
                <dt className={MUTED}>Polls</dt>
                <dd className="niuu:text-text-primary">
                  {compactNumber(environment.pollsCompleted)}
                </dd>
              </div>
              <div>
                <dt className={MUTED}>Signals</dt>
                <dd className="niuu:text-text-primary">
                  {compactNumber(environment.signalsPublished)}
                </dd>
              </div>
              <div>
                <dt className={MUTED}>Tasks</dt>
                <dd className="niuu:text-text-primary">
                  {compactNumber(environment.tasksStarted)}/
                  {compactNumber(environment.tasksCompleted)}
                </dd>
              </div>
              <div>
                <dt className={MUTED}>Learning</dt>
                <dd className="niuu:text-text-primary">
                  {compactNumber(environment.learningEvents)}
                </dd>
              </div>
            </dl>
          </article>
        ))}
        {environments.length === 0 ? <EmptyState label="No live environments observed" /> : null}
      </div>
    </section>
  );
}

function RuntimePanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId?: string;
}) {
  const runtimes = telemetry.runtime.filter(
    (runtime) =>
      !environmentId || environmentId === 'all' || runtime.environmentId === environmentId,
  );
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-live-runtime">
      <div className="niuu:mb-3 niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Shield size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Residents</h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(runtimes.length)} in scope
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {runtimes.map((runtime) => (
          <article
            key={`${runtime.environmentId}:${runtime.valkyrieId}:${runtime.observedAt}`}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-2">
              <div className="niuu:min-w-0">
                <h3 className="niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {runtime.valkyrieName || runtime.valkyrieId || runtime.environmentId}
                </h3>
                <p className="niuu:text-xs niuu:text-text-muted">
                  {runtime.environmentId}
                  {runtime.valkyrieId ? ` · ${runtime.valkyrieId}` : ''} · seen{' '}
                  {formatShortTime(runtime.observedAt)}
                </p>
                {runtime.residentPersonality ? (
                  <p className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">
                    {runtime.residentPersonality}
                  </p>
                ) : null}
              </div>
              <span
                className={`niuu:rounded-full niuu:px-2 niuu:py-1 niuu:text-xs ${
                  runtime.driveLoopEnabled
                    ? 'niuu:bg-success-bg niuu:text-success'
                    : 'niuu:bg-warning-bg niuu:text-warning'
                }`}
              >
                drive {runtime.driveLoopEnabled ? 'on' : 'off'}
              </span>
            </div>
            <dl className="niuu:mt-3 niuu:grid niuu:grid-cols-3 niuu:gap-2 niuu:text-xs">
              <div>
                <dt className={MUTED}>Sources</dt>
                <dd className="niuu:text-text-primary">{compactNumber(runtime.sourceCount)}</dd>
              </div>
              <div>
                <dt className={MUTED}>Poll</dt>
                <dd className="niuu:text-text-primary">{runtime.pollIntervalSeconds}s</dd>
              </div>
              <div>
                <dt className={MUTED}>Initiative</dt>
                <dd className="niuu:text-text-primary">
                  {runtime.initiativeEnabled ? 'on' : 'off'}
                </dd>
              </div>
            </dl>
          </article>
        ))}
        {runtimes.length === 0 ? <EmptyState label="No runtime starts observed" /> : null}
      </div>
    </section>
  );
}

export type LiveView = 'console' | 'topology' | 'lineage' | 'learning' | 'huddles' | 'autonomy';

function LiveMetricGrid({ telemetry }: { telemetry: ValkyrieTelemetry }) {
  const totals = telemetry.totals;
  const activeTasks = Math.max(
    0,
    totals.tasksStarted - totals.tasksCompleted - totals.tasksFailed,
  );
  const dreaming = Math.max(0, totals.dreamCyclesStarted - totals.dreamCyclesCompleted);
  const metrics = [
    {
      label: 'Open signals',
      value: compactNumber(totals.signalsPublished || totals.rawSignalEvents),
      icon: Bell,
    },
    { label: 'Residents', value: compactNumber(telemetry.runtime.length), icon: Shield },
    { label: 'Dreaming', value: compactNumber(dreaming || totals.dreamCyclesStarted), icon: Moon },
    { label: 'Learning in test', value: compactNumber(totals.learningEvents), icon: Brain },
    { label: 'Active actions', value: compactNumber(activeTasks || totals.actions), icon: Zap },
  ];

  return (
    <section
      className="niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:md:grid-cols-5"
      data-testid="valkyrie-live-metrics"
    >
      {metrics.map((metric) => (
        <div key={metric.label} className={PANEL_PAD}>
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:text-text-muted">
            <span className="niuu:text-xs">{metric.label}</span>
            <metric.icon size={15} aria-hidden="true" />
          </div>
          <div className="niuu:mt-1 niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {metric.value}
          </div>
        </div>
      ))}
    </section>
  );
}

function WorkQueuePanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const tasks = (telemetry.recentTasks ?? []).filter(
    (task) => environmentId === 'all' || task.environmentId === environmentId,
  );
  return (
    <section className={`${PANEL_PAD} niuu:min-h-0`} data-testid="valkyrie-work-queue">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <ListChecks size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Work queue</h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(tasks.length)} recent
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {tasks.slice(0, 12).map((task) => (
          <article
            key={`${task.taskId}:${task.status}:${task.observedAt}`}
            className="niuu:grid niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:md:grid-cols-[minmax(0,1fr)_auto]"
          >
            <div className="niuu:min-w-0">
              <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
                <span className="niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
                  {task.title || task.taskId || 'task'}
                </span>
                <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                  {task.status}
                </span>
              </div>
              <p className="niuu:mt-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
                {task.environmentId} · {valueOrNone(task.persona)} · {valueOrNone(task.triggeredBy)}
              </p>
              {task.reason || task.outcome ? (
                <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                  {task.reason || task.outcome}
                </p>
              ) : null}
            </div>
            <span className="niuu:text-xs niuu:text-text-muted">
              {formatShortTime(task.observedAt)}
            </span>
          </article>
        ))}
        {tasks.length === 0 ? <EmptyState label="No task telemetry" /> : null}
      </div>
    </section>
  );
}

function EventLogPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const events = (telemetry.recentEvents ?? []).filter(
    (event) => environmentId === 'all' || event.environmentId === environmentId,
  );
  return (
    <section className={`${PANEL_PAD} niuu:min-h-0`} data-testid="valkyrie-event-log">
      <div className="niuu:mb-3 niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Terminal size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Signal tail</h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          sleipnir · {compactNumber(events.length)} retained
        </span>
      </div>
      <div className="niuu:max-h-[34rem] niuu:overflow-auto niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary">
        {events.slice(0, 80).map((event) => (
          <div
            key={event.id}
            className="niuu:grid niuu:gap-2 niuu:border-0 niuu:border-b niuu:border-solid niuu:border-border niuu:px-3 niuu:py-2 niuu:text-xs niuu:last:border-b-0 niuu:md:grid-cols-[7rem_9rem_minmax(0,1fr)_9rem]"
          >
            <span className="niuu:text-text-muted">{formatShortTime(event.observedAt)}</span>
            <span className="niuu:truncate niuu:text-brand">{event.environmentId}</span>
            <span className="niuu:min-w-0">
              <span className="niuu:mr-2 niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-text-muted">
                {event.kind}
              </span>
              <span className="niuu:text-text-primary">{event.summary}</span>
              <span className="niuu:mt-1 niuu:block niuu:truncate niuu:text-text-muted">
                {event.eventType}
                {event.valkyrieName || event.valkyrieId
                  ? ` · ${event.valkyrieName || event.valkyrieId}`
                  : ''}
                {detailText(event.details) ? ` · ${detailText(event.details)}` : ''}
              </span>
            </span>
            <span className="niuu:truncate niuu:text-text-muted">{valueOrNone(event.source)}</span>
          </div>
        ))}
        {events.length === 0 ? <EmptyState label="No Valkyrie event log entries" /> : null}
      </div>
    </section>
  );
}

function LearningOpsPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const learning = (telemetry.recentLearning ?? []).filter(
    (entry) => environmentId === 'all' || entry.environmentId === environmentId,
  );
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-learning-ops">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Moon size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            Learning and dreams
          </h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(learning.length)} events
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {learning.slice(0, 8).map((entry) => (
          <article key={entry.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
            <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {entry.title}
              </span>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                {entry.status}
              </span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
              {entry.environmentId} · {entry.eventType} · {formatShortTime(entry.observedAt)}
            </p>
            {entry.proposalsCreated || entry.proposalsApplied || entry.proposalsDeferred ? (
              <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                {entry.proposalsCreated ?? 0} proposed · {entry.proposalsApplied ?? 0} applied ·{' '}
                {entry.proposalsDeferred ?? 0} deferred
              </p>
            ) : null}
          </article>
        ))}
        {learning.length === 0 ? (
          <EmptyState label="No verified learning, self-improvement, or dream events" />
        ) : null}
      </div>
    </section>
  );
}

function ToolNeedsPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const toolNeeds = (telemetry.recentToolNeeds ?? []).filter(
    (entry) => environmentId === 'all' || entry.environmentId === environmentId,
  );
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-tool-needs">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Wrench size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Tools</h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(toolNeeds.length)} requests
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {toolNeeds.slice(0, 8).map((need) => (
          <article key={need.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
            <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
              <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {need.capability}
              </span>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                {need.status}
              </span>
            </div>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
              {need.environmentId} · {valueOrNone(need.valkyrieId)} ·{' '}
              {formatShortTime(need.observedAt)}
            </p>
            <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{need.summary}</p>
          </article>
        ))}
        {toolNeeds.length === 0 ? <EmptyState label="No tool requests observed" /> : null}
      </div>
    </section>
  );
}

function GapsPanel({ telemetry }: { telemetry: ValkyrieTelemetry }) {
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-gaps">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:gap-2">
        <AlertTriangle size={16} className="niuu:text-warning" aria-hidden="true" />
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Open gaps</h2>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {telemetry.gaps.map((gap) => (
          <div
            key={gap}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-muted"
          >
            {gap}
          </div>
        ))}
        {telemetry.gaps.length === 0 ? (
          <div className="niuu:rounded-md niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-muted">
            No telemetry gaps in the current window
          </div>
        ) : null}
      </div>
    </section>
  );
}

function LiveScopeRail({
  dashboard,
  telemetry,
  selectedEnvironmentId,
  onSelectEnvironment,
}: {
  dashboard: ValkyrieDashboard;
  telemetry: ValkyrieTelemetry;
  selectedEnvironmentId: string;
  onSelectEnvironment: (environmentId: string) => void;
}) {
  const environments = telemetry.byEnvironment.filter(
    (environment) =>
      environment.pollsCompleted +
        environment.pollFailures +
        environment.signalsCollected +
        environment.tasksEnqueued +
        environment.tasksStarted +
        environment.tasksCompleted +
        environment.tasksDropped +
        environment.judgments +
        environment.actions +
        environment.learningEvents +
        environment.dreamCycles >
      0,
  );
  return (
    <aside
      className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:gap-3 niuu:overflow-auto niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-3"
      data-testid="valkyrie-live-scope-rail"
    >
      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-1">
        <button
          type="button"
          className="niuu:rounded niuu:bg-brand/15 niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-medium niuu:text-brand"
        >
          Environments
        </button>
        <button
          type="button"
          className="niuu:rounded niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-muted"
        >
          Flocks
        </button>
      </div>
      <div className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-wide niuu:text-text-muted">
        Where Valkyries Live
      </div>
      <button
        type="button"
        aria-pressed={selectedEnvironmentId === 'all'}
        onClick={() => onSelectEnvironment('all')}
        className={`niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:px-3 niuu:py-2 niuu:text-left ${
          selectedEnvironmentId === 'all'
            ? 'niuu:border-brand niuu:bg-brand/12'
            : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
        }`}
      >
        <span>
          <span className="niuu:block niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            All Valkyries
          </span>
          <span className="niuu:block niuu:text-xs niuu:text-text-muted">
            {compactNumber(environments.length)} environments
          </span>
        </span>
        <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
          {compactNumber(telemetry.totals.eventsObserved)}
        </span>
      </button>
      <div className="niuu:grid niuu:gap-2">
        {environments.map((environment) => {
          const selected = selectedEnvironmentId === environment.environmentId;
          return (
            <button
              key={environment.environmentId}
              type="button"
              aria-pressed={selected}
              onClick={() => onSelectEnvironment(environment.environmentId)}
              className={`niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:px-3 niuu:py-3 niuu:text-left ${
                selected
                  ? 'niuu:border-brand niuu:bg-brand/12'
                  : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
              }`}
            >
              <span className="niuu:min-w-0">
                <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {environmentName(dashboard, environment.environmentId)}
                </span>
                <span className="niuu:block niuu:truncate niuu:text-xs niuu:text-text-muted">
                  {environment.environmentId} · {compactNumber(environment.tasksEnqueued)} queued
                </span>
              </span>
              <span className="niuu:flex niuu:flex-col niuu:items-end niuu:gap-1">
                <span className="niuu:h-2 niuu:w-2 niuu:rounded-full niuu:bg-brand" />
                <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                  {compactNumber(environment.signalsPublished)}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      <div className="niuu:mt-auto niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border niuu:pt-3 niuu:text-xs niuu:leading-6 niuu:text-text-muted">
        <div>substrate ravn · sleipnir</div>
        <div>memory mimir</div>
        <div>rooms skuld · court odin</div>
      </div>
    </aside>
  );
}

function LlmStatusPanel({ telemetry }: { telemetry: ValkyrieTelemetry }) {
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-llm-status">
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <Cpu size={16} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">LLM backend</h2>
      </div>
      <dl className="niuu:mt-3 niuu:grid niuu:gap-2 niuu:text-xs">
        <div>
          <dt className={MUTED}>Model</dt>
          <dd className="niuu:break-words niuu:text-text-primary">
            {telemetry.llm.model || 'unknown'}
          </dd>
        </div>
        <div>
          <dt className={MUTED}>Reflection model</dt>
          <dd className="niuu:break-words niuu:text-text-primary">
            {telemetry.llm.reflectionModel || 'unknown'}
          </dd>
        </div>
        <div>
          <dt className={MUTED}>Status</dt>
          <dd className="niuu:text-text-primary">
            {telemetry.llm.status} · reflection{' '}
            {telemetry.llm.postSessionReflectionEnabled ? 'on' : 'off'}
          </dd>
        </div>
      </dl>
    </section>
  );
}

function SignalsPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const outcomes = telemetry.recentOutcomes.filter(
    (outcome) => environmentId === 'all' || outcome.environmentId === environmentId,
  );
  const signals = (telemetry.recentEvents ?? []).filter(
    (event) =>
      (environmentId === 'all' || event.environmentId === environmentId) &&
      (event.kind === 'signal' || event.kind === 'event'),
  );
  const rows = [
    ...signals.map((signal) => ({
      id: signal.id,
      title: signal.summary,
      meta: `${signal.environmentId} · ${signal.eventType}`,
      status: signal.kind,
      observedAt: signal.observedAt,
    })),
    ...outcomes.map((outcome) => ({
      id: `${outcome.taskId}:${outcome.type}:${outcome.observedAt}`,
      title: outcome.summary || outcome.recommendedAction || outcome.taskId,
      meta: `${outcome.environmentId} · ${outcome.eventType}`,
      status: outcome.verdict || outcome.tier || outcome.type,
      observedAt: outcome.observedAt,
    })),
  ].sort((left, right) => Date.parse(right.observedAt) - Date.parse(left.observedAt));

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-signals-view">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Bell size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Signals</h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(rows.length)} in scope
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {rows.slice(0, 8).map((row) => (
          <article
            key={row.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-2">
              <div className="niuu:min-w-0">
                <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {row.title}
                </h3>
                <p className="niuu:mt-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
                  {row.meta}
                </p>
              </div>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                {row.status}
              </span>
            </div>
            <p className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">
              {formatShortTime(row.observedAt)}
            </p>
          </article>
        ))}
        {rows.length === 0 ? <EmptyState label="No verified signals in this scope" /> : null}
      </div>
    </section>
  );
}

function CourtPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const outcomes = telemetry.recentOutcomes.filter(
    (outcome) => environmentId === 'all' || outcome.environmentId === environmentId,
  );
  const actions = outcomes.filter((outcome) => outcome.type === 'action');
  return (
    <div className="niuu:grid niuu:gap-3">
      <section className={PANEL_PAD} data-testid="valkyrie-court-panel">
        <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <GitBranch size={16} className="niuu:text-brand" aria-hidden="true" />
            <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">ODIN court</h2>
          </div>
          <span className="niuu:text-xs niuu:text-text-muted">
            {compactNumber(outcomes.length)}
          </span>
        </div>
        <div className="niuu:grid niuu:gap-2">
          {outcomes.slice(0, 4).map((outcome) => (
            <article
              key={`${outcome.taskId}:${outcome.type}:${outcome.observedAt}`}
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
            >
              <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                {outcome.summary || outcome.recommendedAction || outcome.taskId}
              </h3>
              <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:gap-2">
                <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                  {outcome.verdict || outcome.tier || outcome.type}
                </span>
                {typeof outcome.confidence === 'number' ? (
                  <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                    {percent(outcome.confidence)}
                  </span>
                ) : null}
              </div>
            </article>
          ))}
          {outcomes.length === 0 ? <EmptyState label="No verified judgments or actions" /> : null}
        </div>
      </section>
      <section className={PANEL_PAD} data-testid="valkyrie-actions-panel">
        <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <Zap size={16} className="niuu:text-brand" aria-hidden="true" />
            <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Actions</h2>
          </div>
          <span className="niuu:text-xs niuu:text-text-muted">{compactNumber(actions.length)}</span>
        </div>
        <div className="niuu:grid niuu:gap-2">
          {actions.slice(0, 4).map((action) => (
            <article
              key={`${action.taskId}:${action.observedAt}`}
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
            >
              <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                {action.recommendedAction || action.summary || action.taskId}
              </h3>
              <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                {action.environmentId} · {action.tier || 'action'} ·{' '}
                {formatShortTime(action.observedAt)}
              </p>
            </article>
          ))}
          {actions.length === 0 ? <EmptyState label="No verified actions" /> : null}
        </div>
      </section>
    </div>
  );
}

function TopologyView({
  dashboard,
  telemetry,
}: {
  dashboard: ValkyrieDashboard;
  telemetry: ValkyrieTelemetry;
}) {
  const report = dashboard.liveReport;
  return (
    <div className="niuu:grid niuu:gap-3" data-testid="valkyrie-topology-view">
      <LiveEnvironmentMatrix telemetry={telemetry} />
      {report ? (
        <FlockReportPanel dashboard={dashboard} />
      ) : (
        <EmptyState label="No live flock transport report" />
      )}
      <div className="niuu:grid niuu:gap-3 niuu:xl:grid-cols-2">
        {telemetry.byEnvironment.map((environment) => (
          <section key={environment.environmentId} className={PANEL_PAD}>
            <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
              <div className="niuu:min-w-0">
                <h2 className="niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {environmentName(dashboard, environment.environmentId)}
                </h2>
                <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                  {environment.environmentId} · seen {formatShortTime(environment.lastObservedAt)}
                </p>
              </div>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                {compactNumber(environment.signalsPublished)} signals
              </span>
            </div>
            <div className="niuu:mt-3 niuu:grid niuu:grid-cols-[auto_1fr_auto] niuu:items-center niuu:gap-3 niuu:text-xs niuu:text-text-muted">
              <span>signals</span>
              <span className="niuu:h-px niuu:bg-border" />
              <span>tasks {compactNumber(environment.tasksEnqueued)}</span>
              <span>judgments</span>
              <span className="niuu:h-px niuu:bg-border" />
              <span>{compactNumber(environment.judgments)}</span>
              <span>actions</span>
              <span className="niuu:h-px niuu:bg-border" />
              <span>{compactNumber(environment.actions)}</span>
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function LineageView({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const events = (telemetry.recentEvents ?? []).filter(
    (event) => environmentId === 'all' || event.environmentId === environmentId,
  );
  const tasks = telemetry.recentTasks.filter(
    (task) => environmentId === 'all' || task.environmentId === environmentId,
  );
  const outcomes = telemetry.recentOutcomes.filter(
    (outcome) => environmentId === 'all' || outcome.environmentId === environmentId,
  );
  const rows = [
    ...events.map((event) => ({
      id: event.id,
      chain: event.correlationId || event.id,
      kind: event.kind,
      title: event.summary,
      meta: `${event.environmentId} · ${event.eventType}`,
      observedAt: event.observedAt,
    })),
    ...tasks.map((task) => ({
      id: `${task.taskId}:${task.status}:${task.observedAt}`,
      chain: task.taskId,
      kind: 'task',
      title: task.title || task.taskId,
      meta: `${task.environmentId} · ${task.status}`,
      observedAt: task.observedAt,
    })),
    ...outcomes.map((outcome) => ({
      id: `${outcome.taskId}:${outcome.type}:${outcome.observedAt}`,
      chain: outcome.taskId,
      kind: outcome.type,
      title: outcome.summary || outcome.recommendedAction || outcome.taskId,
      meta: `${outcome.environmentId} · ${outcome.verdict || outcome.tier || outcome.type}`,
      observedAt: outcome.observedAt,
    })),
  ].sort((left, right) => Date.parse(right.observedAt) - Date.parse(left.observedAt));

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-lineage-view">
      <div className="niuu:mb-3 niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <GitBranch size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Lineage</h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          {compactNumber(rows.length)} linked events
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2">
        {rows.slice(0, 30).map((row) => (
          <article
            key={row.id}
            className="niuu:grid niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:lg:grid-cols-[10rem_minmax(0,1fr)_11rem]"
          >
            <div className="niuu:min-w-0 niuu:text-xs niuu:text-text-muted">
              <div className="niuu:font-mono niuu:text-brand">{row.kind}</div>
              <div className="niuu:mt-1 niuu:truncate">{row.chain}</div>
            </div>
            <div className="niuu:min-w-0">
              <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                {row.title}
              </h3>
              <p className="niuu:mt-1 niuu:truncate niuu:text-xs niuu:text-text-muted">
                {row.meta}
              </p>
            </div>
            <div className="niuu:text-xs niuu:text-text-muted">
              {formatShortTime(row.observedAt)}
            </div>
          </article>
        ))}
        {rows.length === 0 ? <EmptyState label="No verified lineage events" /> : null}
      </div>
    </section>
  );
}

function HuddlesView({ telemetry }: { telemetry: ValkyrieTelemetry }) {
  return (
    <div className="niuu:grid niuu:gap-3" data-testid="valkyrie-huddles-view">
      <section className={PANEL_PAD}>
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <MessageSquare size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Huddles</h2>
        </div>
        <div className="niuu:mt-3">
          <EmptyState label="No verified huddle messages are being published into telemetry yet" />
        </div>
      </section>
      <GapsPanel telemetry={telemetry} />
    </div>
  );
}

function AutonomyPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const runtimes = telemetry.runtime.filter(
    (runtime) => environmentId === 'all' || runtime.environmentId === environmentId,
  );
  const tiers = [
    { label: 'manual', tier: 'tier 0', body: 'Proposes only; every action waits.' },
    { label: 'supervised', tier: 'tier 1', body: 'Acts on low-risk; escalates medium+.' },
    { label: 'delegated', tier: 'tier 2', body: 'Acts within authority; hard gates escalate.' },
    { label: 'yolo', tier: 'tier 3', body: 'Full autonomy inside versioned rollback bounds.' },
  ];
  return (
    <section className={PANEL_PAD} data-testid="valkyrie-autonomy-panel">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:gap-2">
        <Shield size={16} className="niuu:text-brand" aria-hidden="true" />
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Autonomy</h2>
      </div>
      <div className="niuu:grid niuu:gap-2 niuu:lg:grid-cols-4">
        {tiers.map((tier) => (
          <div
            key={tier.label}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:justify-between niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              <span>{tier.label}</span>
              <span className="niuu:text-xs niuu:text-text-muted">{tier.tier}</span>
            </div>
            <p className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">{tier.body}</p>
          </div>
        ))}
      </div>
      <div className="niuu:mt-3 niuu:grid niuu:gap-2">
        {runtimes.map((runtime) => (
          <article
            key={`${runtime.environmentId}:${runtime.valkyrieId}:${runtime.observedAt}`}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-2">
              <div>
                <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {runtime.valkyrieName || runtime.valkyrieId || runtime.environmentId}
                </h3>
                <p className="niuu:text-xs niuu:text-text-muted">
                  {runtime.environmentId}
                  {runtime.valkyrieId ? ` · ${runtime.valkyrieId}` : ''} ·{' '}
                  {runtime.sourceCount} sources · poll {runtime.pollIntervalSeconds}s
                </p>
              </div>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-brand">
                {runtime.driveLoopEnabled ? 'delegated' : 'manual'}
              </span>
            </div>
            <div className="niuu:mt-3 niuu:flex niuu:flex-wrap niuu:gap-1.5">
              <span className="niuu:rounded niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                initiative {runtime.initiativeEnabled ? 'on' : 'off'}
              </span>
              <span className="niuu:rounded niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                drive {runtime.driveLoopEnabled ? 'on' : 'off'}
              </span>
              <span className="niuu:rounded niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                seen {formatShortTime(runtime.observedAt)}
              </span>
            </div>
          </article>
        ))}
        {runtimes.length === 0 ? <EmptyState label="No runtime authority observed" /> : null}
      </div>
    </section>
  );
}

function LiveConsole({ dashboard, view }: { dashboard: ValkyrieDashboard; view: LiveView }) {
  const telemetry = dashboard.telemetry;
  if (!telemetry?.verified) return null;
  const liveEnvironmentIds = telemetry.byEnvironment.map((environment) => environment.environmentId);
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState(
    () => liveEnvironmentIds[0] ?? 'all',
  );
  const selectedName =
    selectedEnvironmentId === 'all'
      ? 'All Valkyries'
      : environmentName(dashboard, selectedEnvironmentId);

  return (
    <main
      data-testid="valkyrie-live-console"
      className="niuu:h-full niuu:min-h-0 niuu:overflow-auto"
    >
      <div className="niuu:grid niuu:min-h-full niuu:gap-3 niuu:xl:grid-cols-[320px_minmax(0,1fr)]">
        <LiveScopeRail
          dashboard={dashboard}
          telemetry={telemetry}
          selectedEnvironmentId={selectedEnvironmentId}
          onSelectEnvironment={setSelectedEnvironmentId}
        />
        <div className="niuu:flex niuu:min-w-0 niuu:flex-col niuu:gap-3">
          <header className="niuu:flex niuu:flex-wrap niuu:items-end niuu:justify-between niuu:gap-3">
            <div className="niuu:min-w-0">
              <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
                <ChevronLeft size={20} className="niuu:text-brand" aria-hidden="true" />
                <h1 className="niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
                  {selectedName}
                </h1>
              </div>
              <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">
                {selectedEnvironmentId === 'all' ? 'all environments' : selectedEnvironmentId} ·{' '}
                {telemetry.source} · last signal {formatShortTime(telemetry.lastObservedAt)}
              </p>
            </div>
            <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
              <span className="niuu:rounded-full niuu:bg-success-bg niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-success">
                watch
              </span>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-muted">
                updated {formatShortTime(dashboard.updatedAt)}
              </span>
            </div>
          </header>

          {view === 'console' ? (
            <>
              <LiveMetricGrid telemetry={telemetry} />
              <EventLogPanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
              <div className="niuu:grid niuu:gap-3 niuu:xl:grid-cols-[minmax(280px,0.9fr)_minmax(320px,1.15fr)_minmax(280px,0.85fr)]">
                <RuntimePanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
                <SignalsPanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
                <CourtPanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
              </div>
              <div className="niuu:grid niuu:gap-3 niuu:xl:grid-cols-[minmax(0,1fr)_360px]">
                <WorkQueuePanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
                <div className="niuu:grid niuu:gap-3">
                  <LlmStatusPanel telemetry={telemetry} />
                  <GapsPanel telemetry={telemetry} />
                </div>
              </div>
            </>
          ) : null}
          {view === 'topology' ? (
            <TopologyView dashboard={dashboard} telemetry={telemetry} />
          ) : null}
          {view === 'lineage' ? (
            <LineageView telemetry={telemetry} environmentId={selectedEnvironmentId} />
          ) : null}
          {view === 'learning' ? (
            <div className="niuu:grid niuu:gap-3 niuu:xl:grid-cols-[minmax(0,1fr)_360px]">
              <LearningOpsPanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
              <div className="niuu:grid niuu:gap-3">
                <ToolNeedsPanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
                <LlmStatusPanel telemetry={telemetry} />
              </div>
            </div>
          ) : null}
          {view === 'huddles' ? <HuddlesView telemetry={telemetry} /> : null}
          {view === 'autonomy' ? (
            <AutonomyPanel telemetry={telemetry} environmentId={selectedEnvironmentId} />
          ) : null}
        </div>
      </div>
    </main>
  );
}

function FlockReportPanel({ dashboard }: { dashboard: ValkyrieDashboard }) {
  const report = dashboard.liveReport;
  if (!report) return null;

  const totals = [
    { label: 'Messages', value: compactNumber(report.totalMessages) },
    { label: 'Stream', value: report.sharedStream },
    { label: 'Route', value: report.routeSubject },
    { label: 'Mode', value: report.projectionMode },
  ];

  return (
    <section className={PANEL_PAD} data-testid="flock-live-report">
      <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-3">
        <div className="niuu:min-w-0">
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <Database size={16} className="niuu:text-brand" aria-hidden="true" />
            <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              {report.title}
            </h2>
          </div>
          <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
            observed {formatShortTime(report.lastObservedAt)} · {report.status}
          </p>
        </div>
        <div className="niuu:grid niuu:w-full niuu:grid-cols-2 niuu:gap-2 niuu:md:w-auto niuu:md:grid-cols-4">
          {totals.map((item) => (
            <div
              key={item.label}
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2"
            >
              <div className="niuu:text-xs niuu:text-text-muted">{item.label}</div>
              <div className="niuu:mt-1 niuu:truncate niuu:text-sm niuu:font-medium niuu:text-text-primary">
                {item.value}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="niuu:mt-3 niuu:grid niuu:gap-3 niuu:lg:grid-cols-2">
        {report.transports.map((transport) => (
          <article
            key={transport.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-2">
              <div className="niuu:min-w-0">
                <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {transport.label}
                </h3>
                <p className="niuu:truncate niuu:text-xs niuu:text-text-muted">
                  {transport.account} · {transport.streamName}
                </p>
              </div>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                {transport.health}
              </span>
            </div>
            <dl className="niuu:mt-3 niuu:grid niuu:grid-cols-3 niuu:gap-2 niuu:text-xs">
              <div>
                <dt className={MUTED}>Signals</dt>
                <dd className="niuu:text-text-primary">{compactNumber(transport.signalCount)}</dd>
              </div>
              <div>
                <dt className={MUTED}>Judgments</dt>
                <dd className="niuu:text-text-primary">{compactNumber(transport.judgmentCount)}</dd>
              </div>
              <div>
                <dt className={MUTED}>Actions</dt>
                <dd className="niuu:text-text-primary">{compactNumber(transport.actionCount)}</dd>
              </div>
            </dl>
            <div className="niuu:mt-3 niuu:flex niuu:flex-wrap niuu:gap-1.5">
              {transport.consumerFilterSubjects.slice(0, 4).map((subject) => (
                <span
                  key={subject}
                  className="niuu:max-w-full niuu:truncate niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted"
                  title={subject}
                >
                  {subject}
                </span>
              ))}
            </div>
          </article>
        ))}
      </div>

      <div className="niuu:mt-3 niuu:grid niuu:gap-2 niuu:md:grid-cols-3">
        {report.findings.map((finding) => (
          <div
            key={finding}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-muted"
          >
            {finding}
          </div>
        ))}
      </div>
    </section>
  );
}

function ResidentPanel({
  dashboard,
  environmentId,
}: {
  dashboard: ValkyrieDashboard;
  environmentId: string;
}) {
  const slice = selectEnvironmentSlice(dashboard, environmentId);
  const actions = useValkyrieActions();
  return (
    <section className={PANEL_PAD} data-testid="resident-panel">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between">
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Residents</h2>
        <span className={MUTED}>{slice.valkyries.length}</span>
      </div>
      <div className="niuu:grid niuu:gap-3 niuu:2xl:grid-cols-2">
        {slice.valkyries.map((valkyrie) => (
          <div
            key={valkyrie.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
              <div className="niuu:min-w-0">
                <h3 className="niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {valkyrie.name}
                </h3>
                <p className="niuu:truncate niuu:text-xs niuu:text-text-muted">
                  {valkyrie.specialty}
                </p>
              </div>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-brand">
                {valkyrie.wakefulness}
              </span>
            </div>
            <div className="niuu:mt-3 niuu:grid niuu:grid-cols-3 niuu:gap-2 niuu:text-xs">
              <span className={MUTED}>confidence {percent(valkyrie.confidence)}</span>
              <span className={MUTED}>tools {valkyrie.toolCount}</span>
              <span className={MUTED}>{valkyrie.status}</span>
            </div>
            <label className="niuu:mt-3 niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
              Autonomy
              <select
                aria-label={`Autonomy for ${valkyrie.name}`}
                value={valkyrie.autonomyMode}
                onChange={(event) => {
                  actions.updateAutonomy.mutate({
                    valkyrieId: valkyrie.id,
                    mode: event.currentTarget.value as AutonomyMode,
                    reason: 'operator-ui',
                  });
                }}
                className="niuu:flex-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-text-primary"
              >
                <option value="manual">manual</option>
                <option value="supervised">supervised</option>
                <option value="delegated">delegated</option>
                <option value="yolo">yolo</option>
              </select>
            </label>
          </div>
        ))}
        {slice.valkyries.length === 0 ? <EmptyState label="No residents" /> : null}
      </div>
    </section>
  );
}

function EnvironmentStatePanel({
  dashboard,
  environmentId,
}: {
  dashboard: ValkyrieDashboard;
  environmentId: string;
}) {
  const slice = selectEnvironmentSlice(dashboard, environmentId);
  return (
    <section className={PANEL_PAD} data-testid="environment-state-panel">
      <h2 className="niuu:mb-3 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
        Operational State
      </h2>
      <div className="niuu:flex niuu:flex-col niuu:gap-3">
        {slice.operationalStates.map((state) => (
          <div
            key={state.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
              <h3 className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{state.name}</h3>
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                {state.drift}
              </span>
            </div>
            <dl className="niuu:mt-3 niuu:grid niuu:gap-2 niuu:text-xs niuu:md:grid-cols-2">
              <div>
                <dt className={MUTED}>Desired</dt>
                <dd className="niuu:text-text-primary">{state.desired}</dd>
              </div>
              <div>
                <dt className={MUTED}>Observed</dt>
                <dd className="niuu:text-text-primary">{state.observed}</dd>
              </div>
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}

function SignalPanel({
  dashboard,
  environmentId,
}: {
  dashboard: ValkyrieDashboard;
  environmentId: string;
}) {
  const slice = selectEnvironmentSlice(dashboard, environmentId);
  const liveSignals = useValkyrieSignals();
  const visibleLiveSignals = liveSignals.filter((event) => event.environmentId === environmentId);
  return (
    <section className={`${PANEL_PAD} niuu:min-h-0`} data-testid="signal-panel">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between">
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Signals</h2>
        <span className={MUTED}>{visibleLiveSignals.length} live</span>
      </div>
      <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:gap-2">
        {slice.signals.map((signal) => (
          <div
            key={signal.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
                {signal.severity}
              </span>
              <span className="niuu:text-xs niuu:text-text-muted">{signal.source}</span>
              <span className="niuu:text-xs niuu:text-text-muted">
                {formatShortTime(signal.receivedAt)}
              </span>
            </div>
            <p className="niuu:mt-2 niuu:text-sm niuu:text-text-primary">{signal.summary}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function DecisionsPanel({
  dashboard,
  environmentId,
}: {
  dashboard: ValkyrieDashboard;
  environmentId: string;
}) {
  const slice = selectEnvironmentSlice(dashboard, environmentId);
  return (
    <section className={PANEL_PAD} data-testid="decisions-panel">
      <h2 className="niuu:mb-3 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
        Judgments, Court, Actions
      </h2>
      <div className="niuu:grid niuu:gap-3 niuu:xl:grid-cols-3">
        <div>
          <h3 className="niuu:mb-2 niuu:text-xs niuu:font-semibold niuu:text-text-muted">
            Judgments
          </h3>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            {slice.judgments.map((judgment) => (
              <div
                key={judgment.id}
                className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:text-sm niuu:text-text-primary"
              >
                {judgment.verdict} · {percent(judgment.confidence)}
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3 className="niuu:mb-2 niuu:text-xs niuu:font-semibold niuu:text-text-muted">Court</h3>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            {slice.courtDecisions.map((decision) => (
              <div key={decision.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
                <p className="niuu:text-sm niuu:text-text-primary">{decision.title}</p>
                <p className="niuu:text-xs niuu:text-text-muted">
                  {decision.status} · {decision.risk}
                </p>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3 className="niuu:mb-2 niuu:text-xs niuu:font-semibold niuu:text-text-muted">
            Actions
          </h3>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            {slice.actions.map((action) => (
              <div key={action.id} className="niuu:rounded-md niuu:bg-bg-primary niuu:p-3">
                <p className="niuu:text-sm niuu:text-text-primary">{action.title}</p>
                <p className="niuu:text-xs niuu:text-text-muted">{action.status}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function LearningControls({ learning }: { learning: LearningRecord }) {
  const actions = useValkyrieActions();
  const request = { learningId: learning.id, reason: 'operator-ui' };
  return (
    <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
      <button
        type="button"
        title="Adopt learning"
        aria-label={`Adopt ${learning.title}`}
        className={ICON_BUTTON}
        onClick={() => actions.adoptLearning.mutate(request)}
      >
        <Check size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        title="Reject learning"
        aria-label={`Reject ${learning.title}`}
        className={ICON_BUTTON}
        onClick={() => actions.rejectLearning.mutate(request)}
      >
        <X size={15} aria-hidden="true" />
      </button>
      <button
        type="button"
        title="Override canary"
        aria-label={`Override ${learning.title}`}
        className={ICON_BUTTON}
        onClick={() => actions.overrideLearning.mutate(request)}
      >
        <RotateCcw size={15} aria-hidden="true" />
      </button>
    </div>
  );
}

function LearningPanel({
  dashboard,
  environmentId,
  selectedFlockId,
}: {
  dashboard: ValkyrieDashboard;
  environmentId: string;
  selectedFlockId: string | null;
}) {
  const environmentSlice = selectEnvironmentSlice(dashboard, environmentId);
  const flockLearnings = selectedFlockId ? selectFlockLearnings(dashboard, selectedFlockId) : [];
  const learnings = selectedFlockId ? flockLearnings : environmentSlice.learnings;
  return (
    <section className={PANEL_PAD} data-testid="learning-panel">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between">
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Learning</h2>
        <Brain size={16} className="niuu:text-brand" aria-hidden="true" />
      </div>
      <div className="niuu:flex niuu:flex-col niuu:gap-3">
        {learnings.map((learning) => (
          <article
            key={learning.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
              <div className="niuu:min-w-0">
                <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {learning.title}
                </h3>
                <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">{learning.summary}</p>
              </div>
              <LearningControls learning={learning} />
            </div>
            <dl className="niuu:mt-3 niuu:grid niuu:gap-2 niuu:text-xs niuu:md:grid-cols-4">
              <div>
                <dt className={MUTED}>Status</dt>
                <dd className="niuu:text-text-primary">{learning.status}</dd>
              </div>
              <div>
                <dt className={MUTED}>Scope</dt>
                <dd className="niuu:text-text-primary">{learning.scope}</dd>
              </div>
              <div>
                <dt className={MUTED}>Confidence</dt>
                <dd className="niuu:text-text-primary">{percent(learning.confidence)}</dd>
              </div>
              <div>
                <dt className={MUTED}>Transfer risk</dt>
                <dd className="niuu:text-text-primary">{learning.negativeTransferRisk}</dd>
              </div>
            </dl>
          </article>
        ))}
        {learnings.length === 0 ? <EmptyState label="No learning records" /> : null}
      </div>
    </section>
  );
}

function HuddlePanel({
  dashboard,
  environmentId,
}: {
  dashboard: ValkyrieDashboard;
  environmentId: string;
}) {
  const slice = selectEnvironmentSlice(dashboard, environmentId);
  const actions = useValkyrieActions();
  const [draftByHuddle, setDraftByHuddle] = useState<Record<string, string>>({});
  return (
    <section className={PANEL_PAD} data-testid="huddle-panel">
      <div className="niuu:mb-3 niuu:flex niuu:items-center niuu:justify-between">
        <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Huddles</h2>
        <MessageSquare size={16} className="niuu:text-brand" aria-hidden="true" />
      </div>
      <div className="niuu:flex niuu:flex-col niuu:gap-3">
        {slice.huddles.map((huddle) => (
          <div
            key={huddle.id}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
              <div>
                <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {huddle.title}
                </h3>
                <p className="niuu:text-xs niuu:text-text-muted">
                  {huddle.status} · {huddle.participantIds.length} participants
                </p>
              </div>
              <button
                type="button"
                className={BUTTON}
                onClick={() => {
                  if (huddle.joined) actions.leaveHuddle.mutate(huddle.id);
                  else actions.joinHuddle.mutate(huddle.id);
                }}
              >
                {huddle.joined ? 'Leave' : 'Join'}
              </button>
            </div>
            <div className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-2">
              {huddle.messages.map((message) => (
                <div key={message.id} className="niuu:rounded-md niuu:bg-bg-secondary niuu:p-2">
                  <div className="niuu:text-xs niuu:text-text-muted">{message.authorName}</div>
                  <div className="niuu:text-sm niuu:text-text-primary">{message.body}</div>
                </div>
              ))}
            </div>
            <form
              className="niuu:mt-3 niuu:flex niuu:gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                const body = draftByHuddle[huddle.id]?.trim();
                if (!body) return;
                actions.sendHuddleMessage.mutate({ huddleId: huddle.id, body });
                setDraftByHuddle((prev) => ({ ...prev, [huddle.id]: '' }));
              }}
            >
              <input
                aria-label={`Message ${huddle.title}`}
                value={draftByHuddle[huddle.id] ?? ''}
                onChange={(event) => {
                  const nextValue = event.currentTarget.value;
                  setDraftByHuddle((prev) => ({ ...prev, [huddle.id]: nextValue }));
                }}
                className="niuu:min-w-0 niuu:flex-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
              />
              <button type="submit" className={BUTTON}>
                Send
              </button>
            </form>
          </div>
        ))}
        {slice.huddles.length === 0 ? <EmptyState label="No huddles" /> : null}
      </div>
    </section>
  );
}

function MainConsole({
  dashboard,
  defaultView,
}: {
  dashboard: ValkyrieDashboard;
  defaultView: LiveView;
}) {
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState(
    () => dashboard.environments[0]?.id ?? '',
  );
  const [selectedFlockId, setSelectedFlockId] = useState<string | null>(null);
  const selectedEnvironment =
    dashboard.environments.find((environment) => environment.id === selectedEnvironmentId) ??
    dashboard.environments[0] ??
    null;
  const selectedFlock = selectedFlockId
    ? dashboard.flocks.find((flock) => flock.id === selectedFlockId)
    : null;

  const heading = selectedFlock?.name ?? selectedEnvironment?.name ?? 'Valkyries';
  const subheading = selectedFlock
    ? `${selectedFlock.domain} · ${selectedFlock.natsSubject}`
    : selectedEnvironment
      ? `${kindLabel(selectedEnvironment.kind)} · ${selectedEnvironment.health}`
      : 'No environment';

  if (dashboard.telemetry?.verified) return <LiveConsole dashboard={dashboard} view={defaultView} />;
  if (!selectedEnvironment) return <EmptyState label="No environments" />;

  return (
    <div className="niuu:grid niuu:h-full niuu:min-h-0 niuu:gap-3 niuu:xl:grid-cols-[260px_minmax(0,1fr)]">
      <EnvironmentRail
        dashboard={dashboard}
        selectedEnvironmentId={selectedEnvironment.id}
        onSelectEnvironment={(environmentId) => {
          setSelectedEnvironmentId(environmentId);
          setSelectedFlockId(null);
        }}
        selectedFlockId={selectedFlockId}
        onSelectFlock={setSelectedFlockId}
      />
      <main className="niuu:min-w-0 niuu:overflow-auto">
        <div className="niuu:flex niuu:flex-col niuu:gap-3">
          <header className="niuu:flex niuu:flex-wrap niuu:items-end niuu:justify-between niuu:gap-3">
            <div className="niuu:min-w-0">
              <h1 className="niuu:truncate niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
                {heading}
              </h1>
              <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">{subheading}</p>
            </div>
            <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
              <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-muted">
                updated {formatShortTime(dashboard.updatedAt)}
              </span>
            </div>
          </header>
          <KpiStrip dashboard={dashboard} />
          <TelemetryPanel telemetry={dashboard.telemetry} />
          <FlockReportPanel dashboard={dashboard} />
          <div className="niuu:grid niuu:gap-3 niuu:md:grid-cols-2 niuu:xl:grid-cols-[minmax(220px,0.8fr)_minmax(300px,1.15fr)_minmax(260px,0.85fr)]">
            <div className="niuu:flex niuu:flex-col niuu:gap-3">
              <ResidentPanel dashboard={dashboard} environmentId={selectedEnvironment.id} />
              <EnvironmentStatePanel dashboard={dashboard} environmentId={selectedEnvironment.id} />
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-3">
              <SignalPanel dashboard={dashboard} environmentId={selectedEnvironment.id} />
              <DecisionsPanel dashboard={dashboard} environmentId={selectedEnvironment.id} />
            </div>
            <div className="niuu:flex niuu:flex-col niuu:gap-3">
              <LearningPanel
                dashboard={dashboard}
                environmentId={selectedEnvironment.id}
                selectedFlockId={selectedFlockId}
              />
              <HuddlePanel dashboard={dashboard} environmentId={selectedEnvironment.id} />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export function ValkyriePage({ defaultView = 'console' }: { defaultView?: LiveView }) {
  const { data, isLoading, error } = useValkyrieDashboard();
  const errorMessage = useMemo(() => {
    if (!error) return null;
    return error instanceof Error ? error.message : 'Unable to load Valkyries';
  }, [error]);

  if (isLoading) return <LoadingState />;
  if (errorMessage) return <ErrorState message={errorMessage} />;
  if (!data) return <EmptyState label="No Valkyrie data" />;

  return (
    <div
      data-testid="valkyrie-page"
      className="niuu:h-full niuu:min-h-0 niuu:overflow-hidden niuu:bg-bg-primary niuu:p-3"
    >
      <MainConsole dashboard={data} defaultView={defaultView} />
    </div>
  );
}
