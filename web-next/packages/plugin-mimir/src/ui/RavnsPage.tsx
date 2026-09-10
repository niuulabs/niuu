import { useActiveMount } from '../application/useActiveMount';
import { useEffect, useMemo, useState } from 'react';
import { usePluginCtx } from '@niuulabs/plugin-sdk';
import type { VolundrAggregatedLog, VolundrLogParticipant } from '@niuulabs/plugin-volundr';
import { StructuredLogViewer, useSkuldChat } from '@niuulabs/plugin-volundr';
import { StateDot, Chip, SessionChat } from '@niuulabs/ui';
import {
  toRavnBinding,
  useWardenLogs,
  useInstallWarden,
  useObservedWarden,
  useStartWarden,
  useStopWarden,
  useUninstallWarden,
  useWardenDirectory,
  type WardenLogEntry,
  type RavnWardenSummary,
} from '../application/useRavns';
import type { RavnBinding } from '../domain/ravn-binding';
import { formatDuration, formatTimestamp } from './format';

const STATE_PILL: Record<RavnBinding['state'], string> = {
  active: 'niuu:bg-bg-tertiary niuu:text-brand-200',
  idle: 'niuu:bg-bg-tertiary niuu:text-text-muted',
  offline: 'niuu:bg-critical-bg niuu:text-critical',
};

const BTN_BASE =
  'niuu:py-2 niuu:px-4 niuu:bg-bg-secondary niuu:border niuu:border-solid niuu:border-border ' +
  'niuu:rounded-md niuu:text-text-primary niuu:font-sans niuu:text-sm niuu:cursor-pointer ' +
  'niuu:disabled:opacity-50 niuu:disabled:cursor-not-allowed';

const BTN_PRIMARY = `${BTN_BASE} niuu:bg-brand niuu:border-brand niuu:text-bg-primary niuu:font-medium`;
export function toggleSelection(values: string[], value: string, checked: boolean): string[] {
  if (checked) return values.includes(value) ? values : [...values, value];
  return values.filter((entry) => entry !== value);
}

export function formatModelOption(
  id: string,
  model?: { name?: string; vendor?: string; provider?: string; tier?: string },
): string {
  if (!model) return id;
  const parts = [model.name || id, model.vendor || model.provider];
  if (model.tier) parts.push(model.tier);
  return parts.filter(Boolean).join(' · ');
}

type DeploymentKind = 'launchd' | 'systemd' | 'k8s-apply' | 'k8s-gitops';

interface PlacementField {
  label: string;
  value: string;
}

const OBSERVATION_PILL: Record<'running' | 'idle' | 'missing' | 'degraded' | 'unknown', string> = {
  running: 'niuu:bg-bg-tertiary niuu:text-brand-200',
  idle: 'niuu:bg-bg-tertiary niuu:text-text-muted',
  missing: 'niuu:bg-critical-bg niuu:text-critical',
  degraded: 'niuu:bg-warning-bg niuu:text-warning',
  unknown: 'niuu:bg-bg-tertiary niuu:text-text-muted',
};

export function normalizeDeployment(value: string): DeploymentKind | 'unknown' {
  if (
    value === 'launchd' ||
    value === 'systemd' ||
    value === 'k8s-apply' ||
    value === 'k8s-gitops'
  ) {
    return value;
  }
  return 'unknown';
}

export function deploymentLabel(value: string): string {
  switch (normalizeDeployment(value)) {
    case 'launchd':
      return 'This Mac (launchd)';
    case 'systemd':
      return 'Linux user service (systemd --user)';
    case 'k8s-apply':
      return 'Kubernetes (direct apply)';
    case 'k8s-gitops':
      return 'Kubernetes (GitOps)';
    default:
      return value || 'unknown';
  }
}

export function lifecycleCopy(value: string): string {
  switch (normalizeDeployment(value)) {
    case 'launchd':
      return 'Install writes the launch agent and registers it with launchctl. Start and stop control the service on this Mac.';
    case 'systemd':
      return 'Install writes a user unit and enables it with systemd. Start and stop control the user service.';
    case 'k8s-apply':
      return 'Install applies the warden manifests to the cluster. Start and stop scale the deployment up or down.';
    case 'k8s-gitops':
      return 'Install renders the manifest into the GitOps repo. Start and stop change desired replica state in Git so the cluster reconciler can follow.';
    default:
      return 'Install prepares the deployment artifact for this target and lifecycle actions update its running state.';
  }
}

export function installLabel(value: string, installed: boolean): string {
  switch (normalizeDeployment(value)) {
    case 'launchd':
      return installed ? 'Reinstall on this Mac' : 'Install on this Mac';
    case 'systemd':
      return installed ? 'Reinstall user service' : 'Install user service';
    case 'k8s-apply':
      return installed ? 'Re-apply to cluster' : 'Apply to cluster';
    case 'k8s-gitops':
      return installed ? 'Re-render GitOps bundle' : 'Render GitOps bundle';
    default:
      return installed ? 'Reinstall' : 'Install';
  }
}

export function startLabel(value: string, active: boolean): string {
  switch (normalizeDeployment(value)) {
    case 'launchd':
    case 'systemd':
      return active ? 'Running' : 'Start service';
    case 'k8s-apply':
      return active ? 'Scaled up' : 'Scale up';
    case 'k8s-gitops':
      return active ? 'Desired state active' : 'Set desired scale to 1';
    default:
      return active ? 'Running' : 'Start';
  }
}

export function stopLabel(value: string): string {
  switch (normalizeDeployment(value)) {
    case 'launchd':
    case 'systemd':
      return 'Stop service';
    case 'k8s-apply':
      return 'Scale down';
    case 'k8s-gitops':
      return 'Set desired scale to 0';
    default:
      return 'Stop';
  }
}

export function uninstallLabel(value: string): string {
  switch (normalizeDeployment(value)) {
    case 'launchd':
    case 'systemd':
      return 'Remove service';
    case 'k8s-apply':
      return 'Remove from cluster';
    case 'k8s-gitops':
      return 'Remove GitOps manifest';
    default:
      return 'Uninstall';
  }
}

export function toTitleCase(value: string): string {
  return value
    .replace(/([A-Z])/g, ' $1')
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\w/, (letter) => letter.toUpperCase());
}

export function formatDeploymentValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

export function formatConsoleAddress(warden: RavnWardenSummary): string {
  const configuredHost = warden.console.publicHost?.trim() || warden.console.host || '0.0.0.0';
  const host =
    configuredHost === '0.0.0.0'
      ? typeof window !== 'undefined'
        ? window.location.hostname
        : '127.0.0.1'
      : configuredHost;
  return `${host}:${warden.console.port}`;
}

export function consoleTransportProtocol(kind: 'http' | 'ws'): string {
  const secure = typeof window !== 'undefined' && window.location.protocol === 'https:';
  if (kind === 'ws') return secure ? 'wss' : 'ws';
  return secure ? 'https' : 'http';
}

export function consoleGatewayUrl(
  warden: Pick<RavnWardenSummary, 'console'>,
  path: string,
  kind: 'http' | 'ws' = 'http',
): string | null {
  if (!warden.console?.enabled || !warden.console.port) return null;
  return `${consoleTransportProtocol(kind)}://${formatConsoleAddress(
    warden as RavnWardenSummary,
  )}${path}`;
}

export function normalizeLogLevel(level?: string): VolundrAggregatedLog['level'] {
  const normalized = (level ?? '').toLowerCase();
  if (normalized === 'error') return 'error';
  if (normalized === 'warn' || normalized === 'warning') return 'warn';
  if (normalized === 'debug') return 'debug';
  return 'info';
}

export function toStructuredLogs(
  sessionId: string,
  entries: WardenLogEntry[],
  participant: VolundrLogParticipant,
): VolundrAggregatedLog[] {
  return entries.map((entry, index) => ({
    id: entry.id,
    sessionId,
    timestamp: entry.timestamp
      ? Date.parse(entry.timestamp) || Date.now() + index
      : Date.now() + index,
    level: normalizeLogLevel(entry.level),
    participant: participant.id,
    participantLabel: participant.label,
    participantKind: participant.kind,
    source: entry.logger ?? entry.source,
    message: entry.message,
    sequence: index + 1,
    stream: entry.source,
  }));
}

function mergeStructuredLogs(...groups: VolundrAggregatedLog[][]): VolundrAggregatedLog[] {
  return groups
    .flat()
    .sort(
      (left, right) =>
        left.timestamp - right.timestamp ||
        left.sequence - right.sequence ||
        left.participant.localeCompare(right.participant) ||
        left.id.localeCompare(right.id),
    );
}

function repoManifestPath(warden: RavnWardenSummary): string {
  const repoPath = String(warden.deploymentKwargs?.repo_path ?? '').trim();
  if (!repoPath) return '';
  const subdir = String(warden.deploymentKwargs?.manifests_subdir ?? 'wardens')
    .trim()
    .replace(/^\/+|\/+$/g, '');
  return subdir ? `${repoPath}/${subdir}/${warden.id}.yaml` : `${repoPath}/${warden.id}.yaml`;
}

function desiredReplicaLabel(warden: RavnWardenSummary): string {
  if (warden.runtime?.state === 'active') return '1 replica';
  if (warden.runtime?.state === 'offline' && !warden.supervisor?.installed) return 'not rendered';
  return '0 replicas';
}

function observedPlacementFields(warden: RavnWardenSummary): PlacementField[] {
  const fields: PlacementField[] = [];
  const deployment = normalizeDeployment(warden.deployment);
  const supervisor = warden.supervisor;

  switch (deployment) {
    case 'launchd':
      fields.push({ label: 'host', value: 'This Mac' });
      if (supervisor?.serviceLabel)
        fields.push({ label: 'launch agent label', value: supervisor.serviceLabel });
      if (supervisor?.serviceFile)
        fields.push({ label: 'launch agent file', value: supervisor.serviceFile });
      if (supervisor?.configFile)
        fields.push({ label: 'runtime config', value: supervisor.configFile });
      break;
    case 'systemd':
      fields.push({ label: 'host', value: 'Linux user service' });
      if (supervisor?.serviceLabel)
        fields.push({ label: 'unit label', value: supervisor.serviceLabel });
      if (supervisor?.serviceFile)
        fields.push({ label: 'unit file', value: supervisor.serviceFile });
      if (supervisor?.configFile)
        fields.push({ label: 'runtime config', value: supervisor.configFile });
      break;
    case 'k8s-apply':
      fields.push({
        label: 'namespace',
        value: String(warden.deploymentKwargs?.namespace ?? 'ravn'),
      });
      if (supervisor?.serviceLabel)
        fields.push({ label: 'deployment resource', value: supervisor.serviceLabel });
      fields.push({ label: 'desired scale', value: desiredReplicaLabel(warden) });
      if (supervisor?.serviceFile)
        fields.push({ label: 'rendered bundle', value: supervisor.serviceFile });
      if (supervisor?.configFile)
        fields.push({ label: 'config snapshot', value: supervisor.configFile });
      if (warden.deploymentKwargs?.image) {
        fields.push({ label: 'image', value: String(warden.deploymentKwargs.image) });
      }
      break;
    case 'k8s-gitops': {
      fields.push({
        label: 'namespace',
        value: String(warden.deploymentKwargs?.namespace ?? 'ravn'),
      });
      if (warden.deploymentKwargs?.repo_path) {
        fields.push({ label: 'GitOps repo', value: String(warden.deploymentKwargs.repo_path) });
      }
      const manifestPath = repoManifestPath(warden) || supervisor?.serviceFile || '';
      if (manifestPath) {
        fields.push({ label: 'manifest path', value: manifestPath });
      }
      if (supervisor?.serviceLabel) {
        fields.push({ label: 'deployment resource', value: supervisor.serviceLabel });
      }
      fields.push({ label: 'desired scale', value: desiredReplicaLabel(warden) });
      if (warden.deploymentKwargs?.manifests_subdir) {
        fields.push({
          label: 'manifest folder',
          value: String(warden.deploymentKwargs.manifests_subdir),
        });
      }
      if (supervisor?.configFile) {
        fields.push({ label: 'config snapshot', value: supervisor.configFile });
      }
      break;
    }
    default:
      if (supervisor?.serviceFile)
        fields.push({ label: 'service artifact', value: supervisor.serviceFile });
      if (supervisor?.configFile)
        fields.push({ label: 'runtime config', value: supervisor.configFile });
      break;
  }

  if (supervisor?.lastInstallAt) {
    fields.push({ label: 'last install', value: formatTimestamp(supervisor.lastInstallAt) });
  }
  if (supervisor?.startCommand && (deployment === 'launchd' || deployment === 'systemd')) {
    fields.push({ label: 'start command', value: supervisor.startCommand });
  }

  return fields;
}

interface RavnCardProps {
  ravn: RavnBinding;
  onClick: () => void;
}

function RavnCard({ ravn, onClick }: RavnCardProps) {
  return (
    <article
      className="niuu:p-4 niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:bg-bg-secondary niuu:flex niuu:flex-col niuu:gap-3 niuu:cursor-pointer niuu:transition-colors niuu:hover:border-border niuu:focus-visible:outline niuu:focus-visible:outline-2 niuu:focus-visible:outline-brand niuu:focus-visible:outline-offset-2"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick();
      }}
      data-testid="ravn-item"
      aria-label={`Warden ${ravn.ravnId}`}
    >
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <span
          className="niuu:inline-flex niuu:items-center niuu:justify-center niuu:font-mono niuu:text-sm niuu:font-bold niuu:text-text-secondary niuu:bg-bg-tertiary niuu:border niuu:border-border-subtle niuu:uppercase niuu:shrink-0"
          style={{ width: 36, height: 36, borderRadius: 'var(--radius-sm)' }}
          aria-hidden
        >
          {ravn.ravnId.charAt(0)}
          {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
        </span>
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:flex-1 niuu:min-w-0">
          <span className="niuu:font-mono niuu:text-sm niuu:font-semibold niuu:text-text-primary niuu:truncate">
            {ravn.ravnId}
          </span>
          <span
            className={`niuu:text-xs niuu:font-mono niuu:px-2 niuu:rounded-sm niuu:shrink-0 ${STATE_PILL[ravn.state]}`}
            data-testid="ravn-state"
          >
            {ravn.state}
          </span>
        </div>
      </div>

      <div className="niuu:flex niuu:gap-1">
        <Chip tone="muted">{ravn.role}</Chip>
      </div>

      <p
        className="niuu:text-xs niuu:text-text-secondary niuu:m-0 niuu:line-clamp-2"
        data-testid="ravn-bio"
      >
        {ravn.bio}
      </p>

      <div className="niuu:flex niuu:flex-wrap niuu:gap-1">
        {ravn.mountNames.map((m) => (
          <Chip key={m} tone={m === ravn.writeMount ? 'brand' : 'muted'}>
            {m === ravn.writeMount ? `✎ ${m}` : m}
          </Chip>
        ))}
      </div>

      <div className="niuu:flex niuu:items-center niuu:gap-4 niuu:pt-2 niuu:border-t niuu:border-border-subtle niuu:text-xs niuu:font-mono">
        <span className="niuu:text-text-secondary">
          <strong className="niuu:text-text-primary">{ravn.pagesTouched}</strong> pages touched
        </span>
        {ravn.lastDream ? (
          <span className="niuu:text-text-muted" data-testid="ravn-dream">
            last dream {formatTimestamp(ravn.lastDream.timestamp)}
          </span>
        ) : (
          <span className="niuu:text-text-muted niuu:italic" data-testid="ravn-no-dream">
            no dream cycles yet
          </span>
        )}
      </div>

      {ravn.lastDream && (
        <div className="niuu:text-xs niuu:text-text-secondary">
          <strong className="niuu:text-text-primary">{ravn.lastDream.pagesUpdated}</strong> pages ·{' '}
          <strong className="niuu:text-text-primary">{ravn.lastDream.entitiesCreated}</strong>{' '}
          entities · {formatDuration(ravn.lastDream.durationMs)}
        </div>
      )}
    </article>
  );
}

interface RavnProfileProps {
  ravn: RavnBinding;
  warden: RavnWardenSummary;
  stdoutLogs: WardenLogEntry[];
  stderrLogs: WardenLogEntry[];
  logsLoading: boolean;
  logsError: string | null;
  isObserving: boolean;
  observationError: string | null;
  isInstalling: boolean;
  isStarting: boolean;
  isStopping: boolean;
  isUninstalling: boolean;
  actionError: string | null;
  onBack: () => void;
  onInstall: () => void;
  onStart: () => void;
  onStop: () => void;
  onUninstall: () => void;
}

function RavnProfile({
  ravn,
  warden,
  stdoutLogs,
  stderrLogs,
  logsLoading,
  logsError,
  isObserving,
  observationError,
  isInstalling,
  isStarting,
  isStopping,
  isUninstalling,
  actionError,
  onBack,
  onInstall,
  onStart,
  onStop,
  onUninstall,
}: RavnProfileProps) {
  const isInstalled = Boolean(warden.supervisor?.installed);
  const isActive = ravn.state === 'active';
  const deploymentEntries = Object.entries(warden.deploymentKwargs ?? {});
  const placementFields = observedPlacementFields(warden);
  const observation = warden.supervisor?.observation;
  const schedules = warden.schedules ?? {
    dreamCycleCronExpression: '0 3 * * *',
    dreamCyclePollIntervalSeconds: 60,
    sourceTriggerPollIntervalSeconds: 60,
    stalenessTriggerScheduleHours: 6,
  };
  const consoleConfig = warden.console ?? {
    enabled: false,
    host: '0.0.0.0',
    port: 0,
    authMode: 'noop' as const,
    publicHost: '',
  };
  const readMountNames = warden.readMountNames?.length ? warden.readMountNames : ravn.mountNames;
  const writeMountNames = warden.writeMountNames?.length
    ? warden.writeMountNames
    : ravn.writeMount
      ? [ravn.writeMount]
      : [];
  const stdoutParticipant: VolundrLogParticipant = {
    id: `${warden.id}-stdout`,
    label: 'stdout',
    kind: 'service',
  };
  const stderrParticipant: VolundrLogParticipant = {
    id: `${warden.id}-stderr`,
    label: 'stderr',
    kind: 'service',
  };
  const stdoutStructuredLogs = toStructuredLogs(warden.id, stdoutLogs, stdoutParticipant);
  const stderrStructuredLogs = toStructuredLogs(warden.id, stderrLogs, stderrParticipant);
  const mergedStructuredLogs = mergeStructuredLogs(stdoutStructuredLogs, stderrStructuredLogs);
  const consoleAddress = formatConsoleAddress({ ...warden, console: consoleConfig });
  const consoleWsUrl = consoleGatewayUrl({ console: consoleConfig }, '/ws', 'ws');
  const consoleChat = useSkuldChat(consoleWsUrl, { historyMode: 'none' });
  const [activeTab, setActiveTab] = useState<'overview' | 'console' | 'logs'>('overview');

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-6" data-testid="ravn-profile">
      <button
        type="button"
        className="niuu:self-start niuu:bg-transparent niuu:border-none niuu:text-text-muted niuu:text-sm niuu:cursor-pointer niuu:p-0 niuu:hover:text-text-secondary"
        onClick={onBack}
        aria-label="Back to instance maintenance"
      >
        ← Instance maintenance
      </button>

      <div className="niuu:flex niuu:items-center niuu:gap-4 niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg">
        <span
          className="niuu:inline-flex niuu:items-center niuu:justify-center niuu:font-mono niuu:text-xl niuu:font-bold niuu:text-text-secondary niuu:bg-bg-tertiary niuu:border niuu:border-border-subtle niuu:uppercase niuu:shrink-0"
          style={{ width: 48, height: 48, borderRadius: 'var(--radius-sm)' }}
          aria-hidden
        >
          {ravn.ravnId.charAt(0)}
          {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
        </span>
        <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:flex-1">
          <h2 className="niuu:m-0 niuu:text-xl niuu:font-mono">{warden.name}</h2>
          <div className="niuu:flex niuu:items-center niuu:gap-2">
            <Chip tone="muted">{ravn.role}</Chip>
            <span
              className={`niuu:text-xs niuu:font-mono niuu:px-2 niuu:rounded-sm ${STATE_PILL[ravn.state]}`}
              data-testid="ravn-state"
            >
              {ravn.state}
            </span>
            <Chip tone={isInstalled ? 'brand' : 'muted'}>
              {isInstalled ? 'installed' : 'not installed'}
            </Chip>
          </div>
          {ravn.tools.length > 0 && (
            <span
              className="niuu:text-xs niuu:font-mono niuu:text-text-muted"
              data-testid="ravn-tools"
            >
              tools: {ravn.tools.join(' · ')}
            </span>
          )}
        </div>
      </div>

      <div
        className="niuu:flex niuu:flex-nowrap niuu:items-center niuu:gap-1 niuu:overflow-x-auto niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:px-3"
        role="tablist"
        aria-label="Warden detail tabs"
      >
        {(
          [
            ['overview', 'Overview'],
            ['console', 'Console'],
            ['logs', 'Logs'],
          ] as const
        ).map(([tabId, label]) => (
          <button
            key={tabId}
            type="button"
            role="tab"
            aria-selected={activeTab === tabId}
            className={
              activeTab === tabId
                ? 'niuu:flex niuu:items-center niuu:gap-2 niuu:border-b-2 niuu:border-brand niuu:px-3 niuu:py-2.5 niuu:font-mono niuu:text-[13px] niuu:font-medium niuu:text-brand'
                : 'niuu:flex niuu:items-center niuu:gap-2 niuu:border-b-2 niuu:border-transparent niuu:px-3 niuu:py-2.5 niuu:font-mono niuu:text-[13px] niuu:text-text-muted niuu:hover:text-text-secondary'
            }
            onClick={() => setActiveTab(tabId)}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <>
          <section className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg">
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:mb-3">
              <div>
                <h4 className="niuu:m-0 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                  Lifecycle
                </h4>
                <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
                  {lifecycleCopy(warden.deployment)}
                </p>
              </div>
              <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                <button
                  type="button"
                  className={BTN_BASE}
                  onClick={onInstall}
                  disabled={isInstalling}
                >
                  {isInstalling ? 'working…' : installLabel(warden.deployment, isInstalled)}
                </button>
                <button
                  type="button"
                  className={BTN_PRIMARY}
                  onClick={onStart}
                  disabled={isStarting || !isInstalled || isActive}
                >
                  {isStarting ? 'working…' : startLabel(warden.deployment, isActive)}
                </button>
                <button
                  type="button"
                  className={BTN_BASE}
                  onClick={onStop}
                  disabled={isStopping || !isInstalled || !isActive}
                >
                  {isStopping ? 'working…' : stopLabel(warden.deployment)}
                </button>
                <button
                  type="button"
                  className={BTN_BASE}
                  onClick={onUninstall}
                  disabled={isUninstalling || !isInstalled}
                >
                  {isUninstalling ? 'working…' : uninstallLabel(warden.deployment)}
                </button>
              </div>
            </div>

            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:text-sm">
              <div>
                <div className="niuu:text-text-muted">deployment</div>
                <div className="niuu:font-mono niuu:text-text-primary">
                  {deploymentLabel(warden.deployment)}
                </div>
              </div>
              <div>
                <div className="niuu:text-text-muted">persona</div>
                <div className="niuu:font-mono niuu:text-text-primary">{warden.persona}</div>
              </div>
              {deploymentEntries.length > 0 && (
                <div className="niuu:col-span-2">
                  <div className="niuu:text-text-muted">deployment options</div>
                  <div
                    className="niuu:mt-2 niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:text-xs"
                    data-testid="warden-deployment-config"
                  >
                    {deploymentEntries.map(([key, value]) => (
                      <div
                        key={key}
                        className="niuu:p-2 niuu:bg-bg-primary niuu:border niuu:border-border-subtle niuu:rounded-sm"
                      >
                        <div className="niuu:text-text-muted">{toTitleCase(key)}</div>
                        <div className="niuu:font-mono niuu:text-text-primary niuu:break-all">
                          {formatDeploymentValue(value)}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {actionError && (
              <div className="niuu:mt-3 niuu:text-sm niuu:text-critical niuu:bg-critical-bg niuu:border niuu:border-critical-bo niuu:rounded-sm niuu:px-3 niuu:py-2">
                {actionError}
              </div>
            )}
          </section>

          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
            <section
              className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:col-span-2"
              data-testid="warden-observed-placement"
            >
              <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3 niuu:mb-3">
                <div>
                  <h4 className="niuu:m-0 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                    Observed placement
                  </h4>
                  {observation?.detail && (
                    <p className="niuu:m-0 niuu:mt-1 niuu:text-sm niuu:text-text-secondary">
                      {observation.detail}
                    </p>
                  )}
                  {!observation?.detail && !isObserving && !observationError && (
                    <p className="niuu:m-0 niuu:mt-1 niuu:text-sm niuu:text-text-secondary">
                      Live backend updates stream here over SSE while this profile is open.
                    </p>
                  )}
                </div>
                <div className="niuu:flex niuu:items-center niuu:gap-2">
                  {isObserving && (
                    <span className="niuu:text-xs niuu:text-text-muted">refreshing…</span>
                  )}
                  <span
                    className={`niuu:text-xs niuu:font-mono niuu:px-2 niuu:rounded-sm ${
                      OBSERVATION_PILL[observation?.status ?? 'unknown']
                    }`}
                    data-testid="warden-observation-status"
                  >
                    {observation?.status ?? 'unknown'}
                  </span>
                </div>
              </div>
              {observation?.source && (
                <div
                  className="niuu:text-xs niuu:text-text-muted niuu:mb-2"
                  data-testid="warden-observation-source"
                >
                  source: {observation.source}
                  {observation.checkedAt
                    ? ` · checked ${formatTimestamp(observation.checkedAt)}`
                    : ''}
                </div>
              )}
              {observationError && (
                <div className="niuu:mb-3 niuu:text-sm niuu:text-critical niuu:bg-critical-bg niuu:border niuu:border-critical-bo niuu:rounded-sm niuu:px-3 niuu:py-2">
                  {observationError}
                </div>
              )}
              <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:text-xs">
                {observation?.fields?.map((field) => (
                  <div
                    key={`observed-${field.label}`}
                    className="niuu:p-2 niuu:bg-bg-primary niuu:border niuu:border-border-subtle niuu:rounded-sm"
                  >
                    <div className="niuu:text-text-muted">{field.label}</div>
                    <div className="niuu:font-mono niuu:text-text-primary niuu:break-all">
                      {field.value}
                    </div>
                  </div>
                ))}
                {placementFields.map((field) => (
                  <div
                    key={field.label}
                    className="niuu:p-2 niuu:bg-bg-primary niuu:border niuu:border-border-subtle niuu:rounded-sm"
                  >
                    <div className="niuu:text-text-muted">{field.label}</div>
                    <div
                      className="niuu:font-mono niuu:text-text-primary niuu:break-all"
                      data-testid={
                        field.label.includes('label') || field.label === 'deployment resource'
                          ? 'warden-service-label'
                          : undefined
                      }
                    >
                      {field.value}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <section className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg">
              <h4 className="niuu:m-0 niuu:mb-3 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                Runtime config
              </h4>
              <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:text-sm">
                <div className="niuu:flex niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-text-muted">model</span>
                  <span className="niuu:font-mono niuu:text-text-primary">
                    {warden.model || 'claude-sonnet-4-6'}
                  </span>
                </div>
                <div className="niuu:flex niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-text-muted">dream cycle cron</span>
                  <span className="niuu:font-mono niuu:text-text-primary">
                    {warden.features.dreamCycleEnabled
                      ? schedules.dreamCycleCronExpression
                      : 'Disabled'}
                  </span>
                </div>
                <div className="niuu:flex niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-text-muted">source poll</span>
                  <span className="niuu:font-mono niuu:text-text-primary">
                    {schedules.sourceTriggerPollIntervalSeconds}s
                  </span>
                </div>
                <div className="niuu:flex niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-text-muted">staleness cadence</span>
                  <span className="niuu:font-mono niuu:text-text-primary">
                    every {schedules.stalenessTriggerScheduleHours}h
                  </span>
                </div>
                <div className="niuu:flex niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-text-muted">console</span>
                  <span className="niuu:font-mono niuu:text-text-primary">
                    {consoleConfig.enabled ? consoleAddress : 'disabled'}
                  </span>
                </div>
                <div className="niuu:flex niuu:justify-between niuu:gap-3">
                  <span className="niuu:text-text-muted">auth mode</span>
                  <span className="niuu:font-mono niuu:text-text-primary">
                    {consoleConfig.authMode}
                  </span>
                </div>
              </div>
            </section>

            <section className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg">
              <h4 className="niuu:m-0 niuu:mb-3 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                Mount bindings
              </h4>
              <div className="niuu:flex niuu:flex-col niuu:gap-3">
                <div>
                  <div className="niuu:text-xs niuu:text-text-muted niuu:mb-2">Read mounts</div>
                  <div className="niuu:flex niuu:flex-wrap niuu:gap-1">
                    {readMountNames.map((mount) => (
                      <Chip key={`read-${mount}`} tone="muted">
                        {mount}
                      </Chip>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="niuu:text-xs niuu:text-text-muted niuu:mb-2">Write mounts</div>
                  <div className="niuu:flex niuu:flex-wrap niuu:gap-1">
                    {writeMountNames.map((mount) => (
                      <Chip key={`write-${mount}`} tone="brand">
                        ✎ {mount}
                      </Chip>
                    ))}
                  </div>
                </div>
              </div>
            </section>

            <section
              className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg"
              data-testid="ravn-expertise"
            >
              <h4 className="niuu:m-0 niuu:mb-3 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                Areas of expertise
              </h4>
              {ravn.expertise.length > 0 ? (
                <div className="niuu:flex niuu:flex-wrap niuu:gap-1">
                  {ravn.expertise.map((e) => (
                    <Chip key={e} tone="brand">
                      {e}
                    </Chip>
                  ))}
                </div>
              ) : (
                <p className="niuu:text-sm niuu:text-text-muted niuu:italic niuu:m-0">
                  no expertise defined
                </p>
              )}
            </section>

            {ravn.lastDream ? (
              <section
                className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg"
                data-testid="ravn-dream"
              >
                <h4 className="niuu:m-0 niuu:mb-3 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                  Last dream
                </h4>
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {(
                    [
                      ['time', formatTimestamp(ravn.lastDream.timestamp), false],
                      ['pages updated', String(ravn.lastDream.pagesUpdated), true],
                      ['entities created', String(ravn.lastDream.entitiesCreated), true],
                      ['lint fixes', String(ravn.lastDream.lintFixes), true],
                      ['duration', formatDuration(ravn.lastDream.durationMs), false],
                    ] as [string, string, boolean][]
                  ).map(([label, value, bold]) => (
                    <div
                      key={label}
                      className="niuu:flex niuu:justify-between niuu:items-baseline niuu:py-[3px] niuu:border-b niuu:border-border-subtle niuu:text-xs niuu:last:border-b-0"
                    >
                      <span className="niuu:text-text-muted">{label}</span>
                      {bold ? (
                        <strong className="niuu:text-text-primary">{value}</strong>
                      ) : (
                        <span className="niuu:font-mono niuu:text-text-secondary">{value}</span>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            ) : (
              <section
                className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg"
                data-testid="ravn-no-dream"
              >
                <h4 className="niuu:m-0 niuu:mb-3 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                  Last dream
                </h4>
                <p className="niuu:text-sm niuu:text-text-muted niuu:italic niuu:m-0">
                  no dream cycles yet
                </p>
              </section>
            )}
          </div>
        </>
      )}

      {activeTab === 'console' && (
        <section
          className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg"
          data-testid="warden-live-console"
        >
          <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3 niuu:mb-3">
            <div>
              <h4 className="niuu:m-0 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                Live console
              </h4>
              <p className="niuu:m-0 niuu:mt-1 niuu:text-sm niuu:text-text-secondary">
                Join the Warden&apos;s own gateway in-page. This is a live operator connection to
                the daemon, not a Völundr session.
              </p>
            </div>
            <span
              className={`niuu:text-xs niuu:font-mono niuu:px-2 niuu:rounded-sm ${
                !consoleWsUrl
                  ? 'niuu:bg-bg-tertiary niuu:text-text-muted'
                  : consoleChat.connected
                    ? 'niuu:bg-bg-tertiary niuu:text-brand-200'
                    : 'niuu:bg-bg-tertiary niuu:text-warning'
              }`}
            >
              {!consoleWsUrl ? 'disabled' : consoleChat.connected ? 'connected' : 'connecting'}
            </span>
          </div>

          {consoleWsUrl ? (
            <div className="niuu:h-[640px] niuu:overflow-hidden niuu:rounded-md niuu:border niuu:border-border-subtle">
              <SessionChat
                messages={consoleChat.messages}
                streamingContent={consoleChat.streamingContent}
                streamingParts={consoleChat.streamingParts}
                streamingModel={consoleChat.streamingModel}
                connected={consoleChat.connected}
                historyLoaded={consoleChat.historyLoaded}
                participants={consoleChat.participants}
                meshEvents={consoleChat.meshEvents}
                agentEvents={consoleChat.agentEvents}
                pendingPermissions={consoleChat.pendingPermissions}
                capabilities={consoleChat.capabilities}
                sessionName={`${warden.name} console`}
                chatEndpoint={null}
                onSend={consoleChat.sendMessage}
                onSendDirected={consoleChat.sendDirectedMessages}
                onStop={consoleChat.sendInterrupt}
                onClear={consoleChat.clearMessages}
                onSetModel={consoleChat.sendSetModel}
                onSetThinkingTokens={consoleChat.sendSetThinkingTokens}
                onRewindFiles={consoleChat.sendRewindFiles}
                onSetInternalVisibility={consoleChat.sendSetInternalVisibility}
                onPermissionRespond={consoleChat.respondToPermission}
              />
            </div>
          ) : (
            <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">
              Enable the live console gateway and assign a port to connect here.
            </p>
          )}
        </section>
      )}

      {activeTab === 'logs' && (
        <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
          <section
            className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:col-span-2"
            data-testid="warden-daemon-log"
          >
            <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:mb-3">
              <h4 className="niuu:m-0 niuu:text-xs niuu:uppercase niuu:tracking-widest niuu:text-text-muted">
                Daemon logs
              </h4>
              <div className="niuu:flex niuu:flex-col niuu:items-end niuu:gap-1">
                {warden.supervisor?.stdoutLog && (
                  <span className="niuu:text-[11px] niuu:font-mono niuu:text-text-faint">
                    stdout: {warden.supervisor.stdoutLog}
                  </span>
                )}
                {warden.supervisor?.stderrLog && (
                  <span className="niuu:text-[11px] niuu:font-mono niuu:text-text-faint">
                    stderr: {warden.supervisor.stderrLog}
                  </span>
                )}
              </div>
            </div>
            <p className="niuu:m-0 niuu:mb-3 niuu:text-sm niuu:text-text-secondary">
              One merged viewer with source filters for stdout and stderr.
            </p>
            {logsError && (
              <div className="niuu:mb-3 niuu:text-sm niuu:text-critical niuu:bg-critical-bg niuu:border niuu:border-critical-bo niuu:rounded-sm niuu:px-3 niuu:py-2">
                {logsError}
              </div>
            )}
            <div className="niuu:h-[320px] niuu:overflow-hidden niuu:rounded-md niuu:border niuu:border-border-subtle">
              <StructuredLogViewer
                logs={mergedStructuredLogs}
                participants={[stdoutParticipant, stderrParticipant]}
                loading={logsLoading}
                emptyText="No daemon log lines yet."
                downloadFilename={`${warden.id}-daemon.log`}
              />
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

export function RavnsPage({ instanceName }: { instanceName?: string } = {}) {
  const ctx = usePluginCtx();
  const active = useActiveMount();
  const mountName = instanceName ?? active.mountName;
  const { data: allWardens, isLoading, isError, error } = useWardenDirectory();
  const wardens = useMemo(
    () =>
      allWardens?.filter(
        (warden) =>
          !mountName ||
          [
            ...warden.mountNames,
            ...(warden.readMountNames ?? []),
            ...(warden.writeMountNames ?? []),
          ].includes(mountName),
      ),
    [allWardens, mountName],
  );
  const installWarden = useInstallWarden();
  const startWarden = useStartWarden();
  const stopWarden = useStopWarden();
  const uninstallWarden = useUninstallWarden();
  const [actionError, setActionError] = useState<string | null>(null);
  const selectedIdFromCtx =
    typeof ctx.tweaks['mimir.selectedWardenId'] === 'string' &&
    ctx.tweaks['mimir.selectedWardenId'].trim().length > 0
      ? (ctx.tweaks['mimir.selectedWardenId'] as string)
      : null;
  const [selectedIdState, setSelectedIdState] = useState<string | null>(selectedIdFromCtx);
  const selectedIdBase = selectedIdFromCtx ?? selectedIdState;
  const setSelectedId = (nextId: string | null) => {
    setSelectedIdState(nextId);
    ctx.setTweak('mimir.selectedWardenId', nextId ?? '');
  };
  const selectedId =
    selectedIdBase && (wardens ?? []).some((warden) => warden.id === selectedIdBase)
      ? selectedIdBase
      : null;
  const observedWardenQuery = useObservedWarden(selectedId);
  const stdoutLogsQuery = useWardenLogs(selectedId, { stream: 'stdout', limit: 120 });
  const stderrLogsQuery = useWardenLogs(selectedId, { stream: 'stderr', limit: 120 });

  const ravns = (wardens ?? []).map(toRavnBinding);
  const selectedWarden = selectedId
    ? (wardens ?? []).find((warden) => warden.id === selectedId)
    : null;
  const profileWarden = observedWardenQuery.data ?? selectedWarden;
  const selectedRavn = profileWarden ? toRavnBinding(profileWarden) : null;

  useEffect(() => {
    if (!selectedIdBase || !wardens) return;
    if (wardens.some((warden) => warden.id === selectedIdBase)) return;
    ctx.setTweak('mimir.selectedWardenId', '');
  }, [ctx, selectedIdBase, wardens]);

  async function handleInstall() {
    if (!selectedWarden) return;
    setActionError(null);
    try {
      await installWarden.mutateAsync(selectedWarden.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'warden install failed');
    }
  }

  async function handleStart() {
    if (!selectedWarden) return;
    setActionError(null);
    try {
      await startWarden.mutateAsync(selectedWarden.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'warden start failed');
    }
  }

  async function handleStop() {
    if (!selectedWarden) return;
    setActionError(null);
    try {
      await stopWarden.mutateAsync(selectedWarden.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'warden stop failed');
    }
  }

  async function handleUninstall() {
    if (!selectedWarden) return;
    setActionError(null);
    try {
      await uninstallWarden.mutateAsync(selectedWarden.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'warden uninstall failed');
    }
  }

  if (profileWarden && selectedRavn) {
    return (
      <div className="niuu:p-6">
        <RavnProfile
          ravn={selectedRavn}
          warden={profileWarden}
          stdoutLogs={stdoutLogsQuery.data ?? []}
          stderrLogs={stderrLogsQuery.data ?? []}
          logsLoading={stdoutLogsQuery.isFetching || stderrLogsQuery.isFetching}
          logsError={
            stdoutLogsQuery.error instanceof Error
              ? stdoutLogsQuery.error.message
              : stderrLogsQuery.error instanceof Error
                ? stderrLogsQuery.error.message
                : null
          }
          isObserving={observedWardenQuery.isFetching}
          observationError={
            observedWardenQuery.error instanceof Error ? observedWardenQuery.error.message : null
          }
          isInstalling={installWarden.isPending}
          isStarting={startWarden.isPending}
          isStopping={stopWarden.isPending}
          isUninstalling={uninstallWarden.isPending}
          actionError={actionError}
          onBack={() => setSelectedId(null)}
          onInstall={() => void handleInstall()}
          onStart={() => void handleStart()}
          onStop={() => void handleStop()}
          onUninstall={() => void handleUninstall()}
        />
      </div>
    );
  }

  return (
    <div className="niuu:p-6">
      <h4>Attached warden</h4>
      <p className="niuu:text-sm niuu:text-text-secondary">
        Inspect maintenance, logs, and the live console for this instance. Enable a warden when
        deploying an instance.
      </p>

      {isLoading && (
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary">
          <StateDot state="processing" pulse />
          <span>loading wardens…</span>
        </div>
      )}

      {isError && (
        <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary">
          <StateDot state="failed" />
          <span>{error instanceof Error ? error.message : 'wardens load failed'}</span>
        </div>
      )}

      {!isLoading && !isError && ravns.length === 0 && (
        <p className="niuu:text-sm niuu:text-text-muted">No warden is attached to this instance.</p>
      )}

      {ravns.length > 0 && (
        <div className="niuu:grid niuu:grid-cols-[repeat(auto-fill,minmax(280px,1fr))] niuu:gap-4">
          {ravns.map((ravn) => (
            <RavnCard key={ravn.ravnId} ravn={ravn} onClick={() => setSelectedId(ravn.ravnId)} />
          ))}
        </div>
      )}
    </div>
  );
}
