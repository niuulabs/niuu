import { useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowDownLeft,
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
  LearningScope,
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

function environmentMatchesSelection(selectedEnvironmentId: string, eventEnvironmentId: string): boolean {
  return (
    selectedEnvironmentId === 'all' ||
    eventEnvironmentId === selectedEnvironmentId ||
    selectedEnvironmentId.endsWith(`-${eventEnvironmentId}`)
  );
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
      !environmentId || environmentMatchesSelection(environmentId, runtime.environmentId),
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
  const dreaming = Math.max(0, totals.dreamCyclesStarted - totals.dreamCyclesCompleted);
  const metrics = [
    {
      label: 'Open signals',
      value: compactNumber(totals.signalsPublished || totals.rawSignalEvents),
      icon: Bell,
    },
    { label: 'Residents', value: compactNumber(telemetry.runtime.length), icon: Shield },
    {
      label: 'Dreams',
      value: compactNumber(
        dreaming || totals.dreamCyclesStarted || totals.dreamCyclesNoop || totals.dreamCyclesCompleted,
      ),
      icon: Moon,
    },
    { label: 'Learning in test', value: compactNumber(totals.learningEvents), icon: Brain },
    {
      label: 'LLM calls',
      value: compactNumber(totals.llmCalls || telemetry.recentOutcomes.length),
      icon: Cpu,
    },
    { label: 'Budget drops', value: compactNumber(totals.budgetDrops || 0), icon: Zap },
  ];

  return (
    <section
      className="niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:md:grid-cols-3 niuu:xl:grid-cols-6"
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
    (task) => environmentMatchesSelection(environmentId, task.environmentId),
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
    (event) => environmentMatchesSelection(environmentId, event.environmentId),
  );
  const logs = (telemetry.recentLogs ?? []).filter((log) =>
    environmentMatchesSelection(environmentId, log.environmentId),
  );
  const rows = [
    ...events.map((event) => ({
      id: event.id,
      observedAt: event.observedAt,
      environmentId: event.environmentId,
      kind: event.kind,
      summary: event.summary,
      meta: [
        event.eventType,
        event.valkyrieName || event.valkyrieId || '',
        detailText(event.details),
      ]
        .filter(Boolean)
        .join(' · '),
      source: event.source || '',
    })),
    ...logs.map((log) => ({
      id: log.id,
      observedAt: log.observedAt,
      environmentId: log.environmentId,
      kind: log.level || 'log',
      summary: log.message,
      meta: [log.eventType, log.valkyrieName || log.valkyrieId || '', log.taskId || '']
        .filter(Boolean)
        .join(' · '),
      source: log.component,
    })),
  ].sort((left, right) => Date.parse(right.observedAt) - Date.parse(left.observedAt));
  return (
    <section className={`${PANEL_PAD} niuu:min-h-0`} data-testid="valkyrie-event-log">
      <div className="niuu:mb-3 niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Terminal size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            Live event and log tail
          </h2>
        </div>
        <span className="niuu:text-xs niuu:text-text-muted">
          NATS · {compactNumber(events.length)} events · {compactNumber(logs.length)} logs
        </span>
      </div>
      <div className="niuu:max-h-[34rem] niuu:overflow-auto niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary">
        {rows.slice(0, 100).map((event) => (
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
                {event.meta}
              </span>
            </span>
            <span className="niuu:truncate niuu:text-text-muted">{valueOrNone(event.source)}</span>
          </div>
        ))}
        {rows.length === 0 ? (
          <EmptyState label="No Valkyrie events or structured logs observed" />
        ) : null}
      </div>
    </section>
  );
}

function EvolutionLoopPanel({
  telemetry,
  environmentId,
}: {
  telemetry: ValkyrieTelemetry;
  environmentId: string;
}) {
  const scopedNeeds = (telemetry.recentToolNeeds ?? []).filter((entry) =>
    environmentMatchesSelection(environmentId, entry.environmentId),
  );
  const scopedLearning = (telemetry.recentLearning ?? []).filter((entry) =>
    environmentMatchesSelection(environmentId, entry.environmentId),
  );
  const totals = telemetry.totals;
  const missing = scopedNeeds.length;
  const proposals = totals.skillProposals || scopedLearning.filter((entry) =>
    entry.eventType.startsWith('self_improvement.') || entry.eventType.startsWith('skill.'),
  ).length;
  const dreams =
    totals.dreamCyclesStarted + (totals.dreamCyclesNoop || 0) + totals.dreamCyclesFailed;
  const gapCount = missing || totals.toolRequests || 0;
  const stages = [
    {
      label: 'Sense',
      value: compactNumber(totals.signalsPublished || totals.rawSignalEvents),
      detail: 'signals observed',
    },
    {
      label: 'Judge',
      value: compactNumber(totals.judgments),
      detail: 'decisions proposed',
    },
    {
      label: 'Gap',
      value: compactNumber(gapCount),
      detail: 'capabilities missing',
    },
    {
      label: 'Evolve',
      value: compactNumber(proposals),
      detail: 'skill/self-improvement proposals',
    },
    {
      label: 'Dream',
      value: compactNumber(dreams),
      detail: 'reflection cycles',
    },
  ];
  const blocked =
    gapCount > 0 && proposals === 0
      ? 'Capability gaps are being noticed, but no skill evolution proposal has been observed.'
      : dreams === 0
        ? 'No dream cycle has been observed for this window.'
        : '';

  return (
    <section className={PANEL_PAD} data-testid="valkyrie-evolution-loop">
      <div className="niuu:mb-3 niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Brain size={16} className="niuu:text-brand" aria-hidden="true" />
          <h2 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            Self-evolution loop
          </h2>
        </div>
        <span
          className={`niuu:rounded-full niuu:px-2 niuu:py-1 niuu:text-xs ${
            blocked
              ? 'niuu:bg-warning-bg niuu:text-warning'
              : 'niuu:bg-success-bg niuu:text-success'
          }`}
        >
          {blocked ? 'stalled' : 'flowing'}
        </span>
      </div>
      <div className="niuu:grid niuu:gap-2 niuu:md:grid-cols-5">
        {stages.map((stage) => (
          <div
            key={stage.label}
            className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
          >
            <div className="niuu:text-xs niuu:text-text-muted">{stage.label}</div>
            <div className="niuu:mt-1 niuu:text-lg niuu:font-semibold niuu:text-text-primary">
              {stage.value}
            </div>
            <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{stage.detail}</div>
          </div>
        ))}
      </div>
      {blocked ? (
        <div className="niuu:mt-3 niuu:rounded-md niuu:border niuu:border-solid niuu:border-warning niuu:bg-warning-bg niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-warning">
          {blocked}
        </div>
      ) : null}
      <div className="niuu:mt-3 niuu:grid niuu:gap-2">
        {scopedNeeds.slice(0, 3).map((need) => (
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
              {need.environmentId} · {need.summary}
            </p>
          </article>
        ))}
        {scopedNeeds.length === 0 ? (
          <EmptyState label="No capability gaps in this scope" />
        ) : null}
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
  const observedByEnvironment = new Map(
    telemetry.byEnvironment.flatMap((environment) => [
      [environment.environmentId, environment],
      [`env-k8s-${environment.environmentId}`, environment],
      [`env-host-${environment.environmentId}`, environment],
      [`env-printer-${environment.environmentId}`, environment],
    ]),
  );
  const environments = dashboard.environments;
  const residentByEnvironment = new Map(
    dashboard.valkyries.map((valkyrie) => [valkyrie.environmentId, valkyrie]),
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
          const observed = observedByEnvironment.get(environment.id);
          const selected = selectedEnvironmentId === environment.id;
          const observedSignals = observed?.signalsPublished ?? 0;
          const queued = observed?.tasksEnqueued ?? 0;
          const resident = residentByEnvironment.get(environment.id);
          return (
            <button
              key={environment.id}
              type="button"
              aria-pressed={selected}
              onClick={() => onSelectEnvironment(environment.id)}
              className={`niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:px-3 niuu:py-3 niuu:text-left ${
                selected
                  ? 'niuu:border-brand niuu:bg-brand/12'
                  : 'niuu:border-border niuu:bg-bg-primary niuu:hover:border-brand/70'
              }`}
            >
              <span className="niuu:min-w-0">
                <span className="niuu:block niuu:truncate niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                  {environment.name}
                </span>
                <span className="niuu:block niuu:truncate niuu:text-xs niuu:text-text-muted">
                  {kindLabel(environment.kind)}
                  {resident?.name ? ` · ${resident.name}` : ''}
                  {' · '}
                  {resident?.identitySource === 'observed'
                    ? 'observed resident'
                    : observed
                      ? 'observed events'
                      : 'configured only'}
                  {queued > 0 ? ` · ${compactNumber(queued)} queued` : ''}
                </span>
              </span>
              <span className="niuu:flex niuu:flex-col niuu:items-end niuu:gap-1">
                <span
                  className={`niuu:h-2 niuu:w-2 niuu:rounded-full ${
                    observed ? 'niuu:bg-brand' : 'niuu:bg-text-muted'
                  }`}
                />
                <span className="niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted">
                  {compactNumber(observedSignals || environment.signalCount)}
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
          <dt className={MUTED}>Observed calls</dt>
          <dd className="niuu:text-text-primary">
            {compactNumber(telemetry.totals.llmCalls || 0)} calls ·{' '}
            {compactNumber(telemetry.totals.llmTokens || 0)} tokens
          </dd>
        </div>
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
    (outcome) => environmentMatchesSelection(environmentId, outcome.environmentId),
  );
  const signals = (telemetry.recentEvents ?? []).filter(
    (event) =>
      (environmentMatchesSelection(environmentId, event.environmentId)) &&
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
    (outcome) => environmentMatchesSelection(environmentId, outcome.environmentId),
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
    (event) => environmentMatchesSelection(environmentId, event.environmentId),
  );
  const tasks = telemetry.recentTasks.filter(
    (task) => environmentMatchesSelection(environmentId, task.environmentId),
  );
  const outcomes = telemetry.recentOutcomes.filter(
    (outcome) => environmentMatchesSelection(environmentId, outcome.environmentId),
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
    (runtime) => environmentMatchesSelection(environmentId, runtime.environmentId),
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
  const firstObservedEnvironmentId =
    dashboard.environments.find((environment) =>
      liveEnvironmentIds.some((environmentId) =>
        environmentMatchesSelection(environment.id, environmentId),
      ),
    )?.id ??
    dashboard.environments[0]?.id ??
    liveEnvironmentIds[0] ??
    'all';
  const [selectedEnvironmentId, setSelectedEnvironmentId] = useState(
    () => firstObservedEnvironmentId,
  );
  const selectedName =
    selectedEnvironmentId === 'all'
      ? 'All Valkyries'
      : dashboard.environments.some((environment) => environment.id === selectedEnvironmentId)
        ? environmentName(dashboard, selectedEnvironmentId)
        : 'Observed environment';

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
                {selectedEnvironmentId === 'all'
                  ? 'all environments'
                  : `observed id ${selectedEnvironmentId}`}{' '}
                · {telemetry.source} · last signal {formatShortTime(telemetry.lastObservedAt)}
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
              <EvolutionLoopPanel
                telemetry={telemetry}
                environmentId={selectedEnvironmentId}
              />
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
            <FlockLearningExchange dashboard={dashboard} />
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

function LearningDecisionButtons({ learning }: { learning: LearningRecord }) {
  const actions = useValkyrieActions();
  const request = { learningId: learning.id, reason: 'operator-learning-exchange' };
  const targetScope = learning.targetScope ?? nextLearningScope(learning.scope);
  const demoteScope = previousLearningScope(learning.scope);
  return (
    <div className="niuu:flex niuu:flex-wrap niuu:justify-end niuu:gap-2">
      <button
        type="button"
        className={BUTTON}
        onClick={() => actions.adoptLearning.mutate(request)}
      >
        <Check size={15} aria-hidden="true" />
        adopt
      </button>
      <button
        type="button"
        className={BUTTON}
        onClick={() => actions.rejectLearning.mutate(request)}
      >
        <X size={15} aria-hidden="true" />
        reject
      </button>
      <button
        type="button"
        className={BUTTON}
        onClick={() => actions.overrideLearning.mutate(request)}
      >
        <RotateCcw size={15} aria-hidden="true" />
        override
      </button>
      <button
        type="button"
        className={BUTTON}
        onClick={() =>
          actions.canaryLearning.mutate({
            ...request,
            canaryEnvironmentId: learning.sourceEnvironmentId,
          })
        }
      >
        <Activity size={15} aria-hidden="true" />
        canary
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={learning.scope === 'shared'}
        onClick={() =>
          actions.promoteLearning.mutate({
            ...request,
            targetScope,
          })
        }
      >
        <GitBranch size={15} aria-hidden="true" />
        promote
      </button>
      <button
        type="button"
        className={BUTTON}
        disabled={learning.scope === 'private'}
        onClick={() =>
          actions.demoteLearning.mutate({
            ...request,
            targetScope: demoteScope,
          })
        }
      >
        <ArrowDownLeft size={15} aria-hidden="true" />
        demote
      </button>
      <button
        type="button"
        className={BUTTON}
        onClick={() => actions.rollbackLearning.mutate(request)}
      >
        <RotateCcw size={15} aria-hidden="true" />
        rollback
      </button>
    </div>
  );
}

const LEARNING_FILTERS: Array<LearningRecord['status'] | 'all'> = [
  'all',
  'requested',
  'candidate',
  'canary',
  'adopted',
  'rejected',
  'rolled_back',
  'completed',
];

const LEARNING_SCOPES: LearningScope[] = ['private', 'environment', 'domain', 'flock', 'shared'];

function nextLearningScope(scope: LearningScope): LearningScope {
  const index = LEARNING_SCOPES.indexOf(scope);
  return LEARNING_SCOPES[Math.min(index + 1, LEARNING_SCOPES.length - 1)] ?? 'environment';
}

function previousLearningScope(scope: LearningScope): LearningScope {
  const index = LEARNING_SCOPES.indexOf(scope);
  return LEARNING_SCOPES[Math.max(index - 1, 0)] ?? 'private';
}

function artifactLabel(learning: LearningRecord): string {
  if (learning.artifactType) return learning.artifactType.replaceAll('_', ' ');
  if (learning.promotedTool) return 'skill/tool artifact';
  return 'no artifact attached';
}

function artifactRuntimeEffect(learning: LearningRecord): string {
  if (!learning.artifactContent && !learning.promotedTool) {
    return 'No generated skill/tool content is attached yet.';
  }
  if (learning.active) {
    return 'Eligible for subsequent signal handling by capability match.';
  }
  return 'Reviewable only; not eligible for runtime use until adopted or canaried.';
}

function commandDeliveryText(learning: LearningRecord): string {
  if (learning.commandDelivery?.published) {
    return `notified via ${learning.commandDelivery.eventType ?? 'Sleipnir event'}`;
  }
  if (learning.commandDelivery?.message) return learning.commandDelivery.message;
  return 'No learning command has been published from this dashboard action yet.';
}

function learningStatusClass(status: LearningRecord['status']): string {
  if (status === 'rolled_back' || status === 'rejected') {
    return 'niuu:border-critical/60 niuu:bg-critical-bg niuu:text-critical';
  }
  if (status === 'adopted') {
    return 'niuu:border-border niuu:bg-bg-tertiary niuu:text-text-primary';
  }
  return 'niuu:border-brand/60 niuu:bg-brand/12 niuu:text-brand';
}

function transferRiskClass(risk: LearningRecord['negativeTransferRisk']): string {
  if (risk === 'high') return 'niuu:text-critical';
  if (risk === 'medium') return 'niuu:text-brand';
  return 'niuu:text-text-primary';
}

function learningSourceLine(dashboard: ValkyrieDashboard, learning: LearningRecord): string {
  const environment =
    dashboard.environments.find((entry) => entry.id === learning.sourceEnvironmentId)?.name ??
    learning.sourceEnvironmentId;
  const valkyrie =
    dashboard.valkyries.find((entry) => entry.id === learning.sourceValkyrieId)?.name ??
    learning.sourceValkyrieId;
  const flock =
    dashboard.flocks.find((entry) => entry.id === learning.targetFlockId)?.name ??
    learning.targetFlockId ??
    'local';
  return `${environment} > ${valkyrie} > ${flock}`;
}

function LearningScopeLadder({ learning }: { learning: LearningRecord }) {
  const activeScope = learning.currentScope ?? learning.scope;
  return (
    <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3 niuu:text-sm">
      <span className="niuu:font-semibold niuu:uppercase niuu:tracking-wide niuu:text-text-muted">
        Scope ladder
      </span>
      {LEARNING_SCOPES.map((scope, index) => {
        const isCurrent = scope === activeScope;
        const isAvailable = (learning.availableScopes ?? [learning.scope]).includes(scope);
        return (
          <div key={scope} className="niuu:flex niuu:items-center niuu:gap-3">
            <span
              className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-3 niuu:py-1 ${
                isCurrent
                  ? 'niuu:border-brand niuu:bg-brand/18 niuu:text-text-primary'
                  : isAvailable
                    ? 'niuu:border-brand/60 niuu:bg-brand/12 niuu:text-brand'
                    : 'niuu:border-border niuu:bg-bg-secondary niuu:text-text-muted'
              }`}
            >
              {scope}
            </span>
            {index < LEARNING_SCOPES.length - 1 ? (
              <span className="niuu:text-text-muted">-&gt;</span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function LearningReviewDrawer({
  dashboard,
  learning,
  onClose,
}: {
  dashboard: ValkyrieDashboard;
  learning: LearningRecord;
  onClose: () => void;
}) {
  return (
    <div
      className="niuu:fixed niuu:inset-0 niuu:z-50 niuu:grid niuu:bg-bg-primary/75 niuu:backdrop-blur-sm niuu:lg:grid-cols-[1fr_minmax(560px,760px)]"
      role="dialog"
      aria-modal="true"
      aria-label={`Review ${learning.title}`}
    >
      <button
        type="button"
        className="niuu:hidden niuu:border-0 niuu:bg-transparent niuu:lg:block"
        aria-label="Close learning review"
        onClick={onClose}
      />
      <aside className="niuu:h-full niuu:overflow-y-auto niuu:border-0 niuu:border-l niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-5">
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-4">
          <div className="niuu:min-w-0">
            <p className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-wide niuu:text-brand">
              Learning review
            </p>
            <h2 className="niuu:mt-2 niuu:text-2xl niuu:font-bold niuu:text-text-primary">
              {learning.title}
            </h2>
            <p className="niuu:mt-2 niuu:text-sm niuu:text-text-muted">
              {learningSourceLine(dashboard, learning)}
            </p>
          </div>
          <button type="button" className={ICON_BUTTON} aria-label="Close" onClick={onClose}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="niuu:mt-5 niuu:flex niuu:flex-wrap niuu:gap-2">
          <span className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-3 niuu:py-1 niuu:text-sm niuu:font-semibold ${learningStatusClass(learning.status)}`}>
            {learning.status.replace('_', ' ')}
          </span>
          <span className="niuu:rounded-full niuu:border niuu:border-solid niuu:border-brand/60 niuu:bg-brand/12 niuu:px-3 niuu:py-1 niuu:text-sm niuu:text-brand">
            scope {learning.scope}
          </span>
          <span className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-3 niuu:py-1 niuu:text-sm ${learning.active ? 'niuu:border-brand/60 niuu:text-brand' : 'niuu:border-border niuu:text-text-muted'}`}>
            {learning.active ? 'active in runtime' : 'inactive in runtime'}
          </span>
          <span className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-3 niuu:py-1 niuu:text-sm ${learning.artifactContent || learning.promotedTool ? 'niuu:border-brand/60 niuu:text-brand' : 'niuu:border-border niuu:text-text-muted'}`}>
            artifact {artifactLabel(learning)}
          </span>
          <span className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-3 niuu:py-1 niuu:text-sm ${learning.commandDelivery?.published ? 'niuu:border-brand/60 niuu:text-brand' : 'niuu:border-border niuu:text-text-muted'}`}>
            {learning.commandDelivery?.published ? 'command published' : 'command local'}
          </span>
        </div>

        <div className="niuu:mt-5">
          <LearningScopeLadder learning={learning} />
        </div>

        <div className="niuu:mt-5 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-4">
          <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            What this changes
          </h3>
          <p className="niuu:mt-2 niuu:text-sm niuu:leading-6 niuu:text-text-muted">
            Adopt/canary makes this learning eligible for subsequent signal handling. Reject and
            rollback make it ineligible. Promote/demote changes the scope peers may consider, one
            step at a time. The API publishes canonical Sleipnir learning events when a command
            publisher is configured.
          </p>
          <p className="niuu:mt-2 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            {artifactRuntimeEffect(learning)}
          </p>
          <p className="niuu:mt-2 niuu:text-sm niuu:text-text-muted">
            command delivery: {commandDeliveryText(learning)}
          </p>
        </div>

        <div className="niuu:mt-5">
          <LearningDecisionButtons learning={learning} />
        </div>

        <section className="niuu:mt-6 niuu:grid niuu:gap-4">
          <div className={PANEL_PAD}>
            <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Summary</h3>
            <p className="niuu:mt-2 niuu:text-sm niuu:leading-6 niuu:text-text-muted">
              {learning.summary}
            </p>
            <p className="niuu:mt-3 niuu:text-sm niuu:text-text-primary">
              {learning.evaluation || 'No replay evaluation recorded yet.'}
            </p>
          </div>

          <div className={PANEL_PAD}>
            <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              Generated artifact
            </h3>
            <p className="niuu:mt-2 niuu:text-xs niuu:text-text-muted">
              {artifactLabel(learning)} {learning.artifactPath ? `· ${learning.artifactPath}` : ''}
            </p>
            <pre className="niuu:mt-3 niuu:max-h-80 niuu:overflow-auto niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:leading-5 niuu:text-text-primary">
              {learning.artifactContent || 'No artifact content attached to this learning yet.'}
            </pre>
          </div>

          <div className={PANEL_PAD}>
            <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              Source evidence
            </h3>
            <p className="niuu:mt-2 niuu:text-sm niuu:text-text-muted">
              signals: {(learning.sourceSignalIds ?? []).join(', ') || 'none recorded'}
            </p>
            <pre className="niuu:mt-3 niuu:max-h-52 niuu:overflow-auto niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:leading-5 niuu:text-text-primary">
              {JSON.stringify(learning.sourceEvidence ?? {}, null, 2)}
            </pre>
          </div>

          <div className={PANEL_PAD}>
            <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
              Odin review
            </h3>
            <p className="niuu:mt-2 niuu:text-sm niuu:text-text-muted">
              {learning.odinReview?.outcome || 'no review recorded'} ·{' '}
              {learning.odinReview?.reviewer || 'unknown reviewer'}
            </p>
            <p className="niuu:mt-2 niuu:text-sm niuu:leading-6 niuu:text-text-primary">
              {learning.odinReview?.rationale || 'No rationale recorded.'}
            </p>
            {(learning.odinReview?.findings ?? []).length > 0 ? (
              <ul className="niuu:mt-2 niuu:pl-5 niuu:text-sm niuu:text-text-muted">
                {learning.odinReview?.findings?.map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className={PANEL_PAD}>
            <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">History</h3>
            <div className="niuu:mt-3 niuu:grid niuu:gap-2">
              {(learning.history ?? []).slice().reverse().map((entry, index) => (
                <div
                  key={`${entry.eventType}-${entry.observedAt}-${index}`}
                  className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
                >
                  <div className="niuu:flex niuu:justify-between niuu:gap-3 niuu:text-xs niuu:text-text-muted">
                    <span>{entry.eventType}</span>
                    <span>{formatShortTime(entry.observedAt)}</span>
                  </div>
                  <p className="niuu:mt-1 niuu:text-sm niuu:text-text-primary">
                    {entry.summary}
                  </p>
                </div>
              ))}
              {(learning.history ?? []).length === 0 ? (
                <p className="niuu:text-sm niuu:text-text-muted">No lifecycle history yet.</p>
              ) : null}
            </div>
          </div>
        </section>
      </aside>
    </div>
  );
}

function FlockLearningExchange({ dashboard }: { dashboard: ValkyrieDashboard }) {
  const [filter, setFilter] = useState<LearningRecord['status'] | 'all'>('all');
  const [selectedLearningId, setSelectedLearningId] = useState<string | null>(null);
  const learnings = dashboard.learnings;
  const selectedLearning =
    learnings.find((learning) => learning.id === selectedLearningId) ?? null;
  const visible =
    filter === 'all' ? learnings : learnings.filter((learning) => learning.status === filter);
  const counts = Object.fromEntries(
    LEARNING_FILTERS.map((status) => [
      status,
      status === 'all'
        ? learnings.length
        : learnings.filter((learning) => learning.status === status).length,
    ]),
  ) as Record<(typeof LEARNING_FILTERS)[number], number>;

  return (
    <section
      className="niuu:min-h-full niuu:bg-bg-primary niuu:p-4 niuu:md:p-6"
      data-testid="valkyrie-learning-exchange"
    >
      <header>
        <div className="niuu:flex niuu:items-center niuu:gap-3">
          <Brain size={34} className="niuu:text-brand" aria-hidden="true" />
          <h1 className="niuu:text-3xl niuu:font-bold niuu:leading-tight niuu:text-text-primary">
            Flock learning exchange
          </h1>
        </div>
        <p className="niuu:mt-2 niuu:text-base niuu:text-text-muted">
          vetted learnings shared across the cohort - candidate, canary, adopted, rejected,
          rolled back
        </p>
      </header>

      <div
        className="niuu:mt-5 niuu:inline-flex niuu:flex-wrap niuu:overflow-hidden niuu:rounded-md niuu:border niuu:border-solid niuu:border-border"
        aria-label="Learning status filter"
      >
        {LEARNING_FILTERS.map((status) => (
          <button
            key={status}
            type="button"
            aria-pressed={filter === status}
            onClick={() => setFilter(status)}
            className={`niuu:border-0 niuu:border-r niuu:border-solid niuu:border-border niuu:px-3 niuu:py-2 niuu:text-sm niuu:last:border-r-0 ${
              filter === status
                ? 'niuu:bg-brand/18 niuu:text-text-primary'
                : 'niuu:bg-bg-secondary niuu:text-text-muted niuu:hover:text-text-primary'
            }`}
          >
            {status.replace('_', ' ')} {counts[status]}
          </button>
        ))}
      </div>

      <div className="niuu:mt-6 niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3 niuu:text-sm">
        <LearningScopeLadder
          learning={{
            ...visible[0],
            id: visible[0]?.id ?? 'scope-reference',
            title: visible[0]?.title ?? 'Scope reference',
            summary: visible[0]?.summary ?? '',
            scope: visible[0]?.scope ?? 'private',
            status: visible[0]?.status ?? 'candidate',
            sourceEnvironmentId: visible[0]?.sourceEnvironmentId ?? '',
            sourceValkyrieId: visible[0]?.sourceValkyrieId ?? '',
            confidence: visible[0]?.confidence ?? 0,
            evaluation: visible[0]?.evaluation ?? '',
            negativeTransferRisk: visible[0]?.negativeTransferRisk ?? 'low',
            redaction: visible[0]?.redaction ?? 'none',
            createdAt: visible[0]?.createdAt ?? '',
            currentScope: visible[0]?.currentScope ?? visible[0]?.scope ?? 'private',
          }}
        />
      </div>

      <div className="niuu:mt-6 niuu:grid niuu:gap-5 niuu:xl:grid-cols-2">
        {visible.map((learning) => (
          <article
            key={learning.id}
            className={`niuu:rounded-md niuu:border niuu:border-solid niuu:bg-bg-primary niuu:p-5 ${
              learning.status === 'rolled_back' || learning.status === 'rejected'
                ? 'niuu:border-critical/60'
                : learning.status === 'canary'
                  ? 'niuu:border-brand/70'
                  : 'niuu:border-border'
            }`}
          >
            <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-4">
              <div className="niuu:min-w-0">
                <h2 className="niuu:text-xl niuu:font-bold niuu:text-text-primary">
                  {learning.title}
                </h2>
                <p className="niuu:mt-2 niuu:text-base niuu:leading-7 niuu:text-text-muted">
                  {learning.summary}
                </p>
              </div>
              <div className="niuu:flex niuu:shrink-0 niuu:flex-col niuu:items-end niuu:gap-2">
                <span
                  className={`niuu:rounded-full niuu:border niuu:border-solid niuu:px-3 niuu:py-1 niuu:text-sm niuu:font-semibold ${learningStatusClass(
                    learning.status,
                  )}`}
                >
                  {learning.status.replace('_', ' ')}
                </span>
                <span className="niuu:rounded-full niuu:border niuu:border-solid niuu:border-brand/60 niuu:bg-brand/12 niuu:px-3 niuu:py-1 niuu:text-sm niuu:text-brand">
                  {learning.scope}
                </span>
              </div>
            </div>

            <div className="niuu:mt-5 niuu:text-sm niuu:text-text-muted">
              source{' '}
              <span className="niuu:font-semibold niuu:text-brand">
                {learningSourceLine(dashboard, learning)}
              </span>
            </div>

            <div className="niuu:mt-4 niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3 niuu:text-sm niuu:text-text-muted">
              <span>confidence</span>
              <span className="niuu:h-1 niuu:w-20 niuu:overflow-hidden niuu:rounded-full niuu:bg-bg-tertiary">
                <span
                  className="niuu:block niuu:h-full niuu:bg-text-muted"
                  style={{ width: `${Math.round(learning.confidence * 100)}%` }}
                />
              </span>
              <span>{percent(learning.confidence)}</span>
              <span
                className={`niuu:rounded-md niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:font-semibold ${transferRiskClass(
                  learning.negativeTransferRisk,
                )}`}
              >
                transfer risk {learning.negativeTransferRisk}
              </span>
              <span className="niuu:rounded-md niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-text-primary">
                redaction {learning.redaction}
              </span>
            </div>

            {learning.promotedTool ? (
              <div className="niuu:mt-4 niuu:flex niuu:flex-wrap niuu:gap-2">
                <span className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-sm niuu:text-text-muted">
                  <Wrench size={14} aria-hidden="true" />
                  {learning.promotedTool}
                </span>
                <span className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-sm niuu:text-text-primary">
                  artifact {artifactLabel(learning)}
                </span>
                <span className={`niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:px-3 niuu:py-1 niuu:text-sm ${
                  learning.artifactContent
                    ? 'niuu:bg-brand/12 niuu:text-brand'
                    : 'niuu:bg-bg-tertiary niuu:text-text-muted'
                }`}
                >
                  {learning.artifactContent ? 'content attached' : 'content missing'}
                </span>
                <span className={`niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:px-3 niuu:py-1 niuu:text-sm ${
                  learning.commandDelivery?.published
                    ? 'niuu:bg-brand/12 niuu:text-brand'
                    : 'niuu:bg-bg-tertiary niuu:text-text-muted'
                }`}
                >
                  {learning.commandDelivery?.published ? 'command published' : 'command local'}
                </span>
              </div>
            ) : null}

            <p className="niuu:mt-3 niuu:text-sm niuu:text-text-muted">
              {artifactRuntimeEffect(learning)}
            </p>
            <p className="niuu:mt-1 niuu:text-sm niuu:text-text-muted">
              {commandDeliveryText(learning)}
            </p>

            {learning.status === 'canary' ? (
              <p className="niuu:mt-4 niuu:text-sm niuu:text-text-muted">
                canarying on <span className="niuu:text-brand">{learning.sourceEnvironmentId}</span>
              </p>
            ) : null}

            <div className="niuu:mt-5 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-4">
              <div className="niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-wide niuu:text-text-muted">
                Evaluation
              </div>
              <p className="niuu:mt-2 niuu:text-base niuu:text-text-primary">
                {learning.evaluation || 'Awaiting replay evidence.'}
              </p>
            </div>

            <div className="niuu:mt-5 niuu:border-0 niuu:border-t niuu:border-solid niuu:border-border niuu:pt-4 niuu:grid niuu:gap-3 niuu:md:grid-cols-[1fr_auto] niuu:md:items-center">
              <span className="niuu:text-sm niuu:text-text-muted">
                {formatShortTime(learning.createdAt)}
              </span>
              <div className="niuu:flex niuu:flex-wrap niuu:justify-end niuu:gap-2">
                <button
                  type="button"
                  className={BUTTON}
                  onClick={() => setSelectedLearningId(learning.id)}
                >
                  <ListChecks size={15} aria-hidden="true" />
                  review
                </button>
                <LearningDecisionButtons learning={learning} />
              </div>
            </div>
          </article>
        ))}
        {visible.length === 0 ? <EmptyState label="No learning records in this filter" /> : null}
      </div>
      {selectedLearning ? (
        <LearningReviewDrawer
          dashboard={dashboard}
          learning={selectedLearning}
          onClose={() => setSelectedLearningId(null)}
        />
      ) : null}
    </section>
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
