import { useMemo, useState } from 'react';
import {
  Activity,
  Bell,
  Brain,
  Check,
  Database,
  GitBranch,
  MessageSquare,
  Moon,
  Radio,
  RotateCcw,
  Shield,
  Users,
  X,
} from 'lucide-react';
import type { AutonomyMode, EnvironmentKind, LearningRecord, ValkyrieDashboard } from '../domain';
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
                <dd className="niuu:text-text-primary">
                  {compactNumber(transport.judgmentCount)}
                </dd>
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

function MainConsole({ dashboard }: { dashboard: ValkyrieDashboard }) {
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

export function ValkyriePage() {
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
      <MainConsole dashboard={data} />
    </div>
  );
}
