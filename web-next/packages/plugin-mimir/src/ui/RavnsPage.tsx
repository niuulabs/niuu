import { useState } from 'react';
import { StateDot, Chip } from '@niuulabs/ui';
import {
  toRavnBinding,
  useCreateWarden,
  useInstallWarden,
  useObservedWarden,
  useStartWarden,
  useStopWarden,
  useUninstallWarden,
  useWardenDirectory,
  type RavnWardenSummary,
} from '../application/useRavns';
import type { RavnBinding } from '../domain/ravn-binding';
import { formatDuration, formatTimestamp } from './format';

const STATE_PILL: Record<RavnBinding['state'], string> = {
  active: 'niuu-bg-bg-tertiary niuu-text-brand-200',
  idle: 'niuu-bg-bg-tertiary niuu-text-text-muted',
  offline: 'niuu-bg-critical-bg niuu-text-critical',
};

const INPUT_BASE =
  'niuu-flex-1 niuu-py-2 niuu-px-3 niuu-bg-bg-primary niuu-border niuu-border-solid niuu-border-border ' +
  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-sm niuu-outline-none niuu-box-border ' +
  'focus:niuu-border-brand';

const BTN_BASE =
  'niuu-py-2 niuu-px-4 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border ' +
  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-sm niuu-cursor-pointer ' +
  'disabled:niuu-opacity-50 disabled:niuu-cursor-not-allowed';

const BTN_PRIMARY = `${BTN_BASE} niuu-bg-brand niuu-border-brand niuu-text-bg-primary niuu-font-medium`;

function splitCsv(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

type DeploymentKind = 'launchd' | 'systemd' | 'k8s-apply' | 'k8s-gitops';

interface PlacementField {
  label: string;
  value: string;
}

const OBSERVATION_PILL: Record<
  'running' | 'idle' | 'missing' | 'degraded' | 'unknown',
  string
> = {
  running: 'niuu-bg-bg-tertiary niuu-text-brand-200',
  idle: 'niuu-bg-bg-tertiary niuu-text-text-muted',
  missing: 'niuu-bg-critical-bg niuu-text-critical',
  degraded: 'niuu-bg-warning-bg niuu-text-warning',
  unknown: 'niuu-bg-bg-tertiary niuu-text-text-muted',
};

function normalizeDeployment(value: string): DeploymentKind | 'unknown' {
  if (value === 'launchd' || value === 'systemd' || value === 'k8s-apply' || value === 'k8s-gitops') {
    return value;
  }
  return 'unknown';
}

function deploymentLabel(value: string): string {
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

function lifecycleCopy(value: string): string {
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

function installLabel(value: string, installed: boolean): string {
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

function startLabel(value: string, active: boolean): string {
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

function stopLabel(value: string): string {
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

function uninstallLabel(value: string): string {
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

function toTitleCase(value: string): string {
  return value
    .replace(/([A-Z])/g, ' $1')
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^\w/, (letter) => letter.toUpperCase());
}

function formatDeploymentValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  return JSON.stringify(value);
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
      if (supervisor?.serviceLabel) fields.push({ label: 'launch agent label', value: supervisor.serviceLabel });
      if (supervisor?.serviceFile) fields.push({ label: 'launch agent file', value: supervisor.serviceFile });
      if (supervisor?.configFile) fields.push({ label: 'runtime config', value: supervisor.configFile });
      break;
    case 'systemd':
      fields.push({ label: 'host', value: 'Linux user service' });
      if (supervisor?.serviceLabel) fields.push({ label: 'unit label', value: supervisor.serviceLabel });
      if (supervisor?.serviceFile) fields.push({ label: 'unit file', value: supervisor.serviceFile });
      if (supervisor?.configFile) fields.push({ label: 'runtime config', value: supervisor.configFile });
      break;
    case 'k8s-apply':
      fields.push({
        label: 'namespace',
        value: String(warden.deploymentKwargs?.namespace ?? 'ravn'),
      });
      if (supervisor?.serviceLabel) fields.push({ label: 'deployment resource', value: supervisor.serviceLabel });
      fields.push({ label: 'desired scale', value: desiredReplicaLabel(warden) });
      if (supervisor?.serviceFile) fields.push({ label: 'rendered bundle', value: supervisor.serviceFile });
      if (supervisor?.configFile) fields.push({ label: 'config snapshot', value: supervisor.configFile });
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
        fields.push({ label: 'manifest folder', value: String(warden.deploymentKwargs.manifests_subdir) });
      }
      if (supervisor?.configFile) {
        fields.push({ label: 'config snapshot', value: supervisor.configFile });
      }
      break;
    }
    default:
      if (supervisor?.serviceFile) fields.push({ label: 'service artifact', value: supervisor.serviceFile });
      if (supervisor?.configFile) fields.push({ label: 'runtime config', value: supervisor.configFile });
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

interface CreateWardenFormProps {
  isCreating: boolean;
  errorMessage: string | null;
  onCancel: () => void;
  onSubmit: (draft: {
    name: string;
    persona: string;
    profile: string;
    deployment: DeploymentKind;
    deploymentKwargs: Record<string, unknown>;
    mounts: string[];
    writeMount: string;
    autostart: boolean;
  }) => void | Promise<void>;
}

function CreateWardenForm({ isCreating, errorMessage, onCancel, onSubmit }: CreateWardenFormProps) {
  const [name, setName] = useState('');
  const [persona, setPersona] = useState('research-and-distill');
  const [profile, setProfile] = useState('');
  const [deployment, setDeployment] = useState<DeploymentKind>('launchd');
  const [mounts, setMounts] = useState('local');
  const [writeMount, setWriteMount] = useState('local');
  const [autostart, setAutostart] = useState(true);
  const [namespace, setNamespace] = useState('ravn');
  const [image, setImage] = useState('ghcr.io/niuulabs/ravn:latest');
  const [serviceAccountName, setServiceAccountName] = useState('');
  const [createNamespace, setCreateNamespace] = useState(false);
  const [repoPath, setRepoPath] = useState('');
  const [manifestsSubdir, setManifestsSubdir] = useState('wardens');
  const [autoCommit, setAutoCommit] = useState(true);
  const [autoPush, setAutoPush] = useState(false);

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const deploymentKwargs: Record<string, unknown> = {};
    if (deployment === 'k8s-apply' || deployment === 'k8s-gitops') {
      deploymentKwargs.namespace = namespace.trim() || 'ravn';
      deploymentKwargs.image = image.trim() || 'ghcr.io/niuulabs/ravn:latest';
      deploymentKwargs.create_namespace = createNamespace;
      if (serviceAccountName.trim()) {
        deploymentKwargs.service_account_name = serviceAccountName.trim();
      }
    }
    if (deployment === 'k8s-gitops') {
      deploymentKwargs.repo_path = repoPath.trim();
      deploymentKwargs.manifests_subdir = manifestsSubdir.trim() || 'wardens';
      deploymentKwargs.auto_commit = autoCommit;
      deploymentKwargs.auto_push = autoPush;
    }

    void onSubmit({
      name,
      persona,
      profile,
      deployment,
      deploymentKwargs,
      mounts: splitCsv(mounts),
      writeMount: writeMount.trim(),
      autostart,
    });
  }

  return (
    <form
      className="niuu-p-4 niuu-mb-6 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-flex niuu-flex-col niuu-gap-3"
      onSubmit={handleSubmit}
      aria-label="Create warden form"
    >
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3">
        <div>
          <h3 className="niuu-m-0 niuu-text-base niuu-font-semibold niuu-text-text-primary">
            Create warden
          </h3>
          <p className="niuu-m-0 niuu-text-sm niuu-text-text-secondary">
            Seed a local Ravn warden that can then be installed and started.
          </p>
        </div>
      </div>

      <label className="niuu-flex niuu-flex-col niuu-gap-1">
        <span className="niuu-text-xs niuu-text-text-muted">Name</span>
        <input
          className={INPUT_BASE}
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Research Warden"
          aria-label="Warden name"
          required
        />
      </label>

      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
        <label className="niuu-flex niuu-flex-col niuu-gap-1">
          <span className="niuu-text-xs niuu-text-text-muted">Persona</span>
          <input
            className={INPUT_BASE}
            value={persona}
            onChange={(event) => setPersona(event.target.value)}
            placeholder="research-and-distill"
            aria-label="Persona"
            required
          />
        </label>
        <label className="niuu-flex niuu-flex-col niuu-gap-1">
          <span className="niuu-text-xs niuu-text-text-muted">Profile</span>
          <input
            className={INPUT_BASE}
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
            placeholder="infra-synthesis"
            aria-label="Profile"
          />
        </label>
      </div>

      <label className="niuu-flex niuu-flex-col niuu-gap-1">
        <span className="niuu-text-xs niuu-text-text-muted">Deployment target</span>
        <select
          className={INPUT_BASE}
          value={deployment}
          onChange={(event) => setDeployment(event.target.value as DeploymentKind)}
          aria-label="Deployment target"
        >
          <option value="launchd">This Mac (`launchd`)</option>
          <option value="systemd">Linux user service (`systemd --user`)</option>
          <option value="k8s-apply">Kubernetes (direct apply)</option>
          <option value="k8s-gitops">Kubernetes (GitOps)</option>
        </select>
      </label>

      {(deployment === 'k8s-apply' || deployment === 'k8s-gitops') && (
        <div className="niuu-p-3 niuu-bg-bg-primary niuu-border niuu-border-border-subtle niuu-rounded-md niuu-flex niuu-flex-col niuu-gap-3">
          <div className="niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
            Kubernetes settings
          </div>
          <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
            <label className="niuu-flex niuu-flex-col niuu-gap-1">
              <span className="niuu-text-xs niuu-text-text-muted">Namespace</span>
              <input
                className={INPUT_BASE}
                value={namespace}
                onChange={(event) => setNamespace(event.target.value)}
                placeholder="ravn"
                aria-label="Kubernetes namespace"
              />
            </label>
            <label className="niuu-flex niuu-flex-col niuu-gap-1">
              <span className="niuu-text-xs niuu-text-text-muted">Image</span>
              <input
                className={INPUT_BASE}
                value={image}
                onChange={(event) => setImage(event.target.value)}
                placeholder="ghcr.io/niuulabs/ravn:latest"
                aria-label="Kubernetes image"
              />
            </label>
            <label className="niuu-flex niuu-flex-col niuu-gap-1 niuu-col-span-2">
              <span className="niuu-text-xs niuu-text-text-muted">Service account</span>
              <input
                className={INPUT_BASE}
                value={serviceAccountName}
                onChange={(event) => setServiceAccountName(event.target.value)}
                placeholder="ravn-warden"
                aria-label="Kubernetes service account"
              />
            </label>
          </div>

          <label className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
            <input
              type="checkbox"
              checked={createNamespace}
              onChange={(event) => setCreateNamespace(event.target.checked)}
              aria-label="Create namespace if missing"
            />
            Create namespace if missing
          </label>

          {deployment === 'k8s-gitops' && (
            <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
              <label className="niuu-flex niuu-flex-col niuu-gap-1 niuu-col-span-2">
                <span className="niuu-text-xs niuu-text-text-muted">GitOps repo path</span>
                <input
                  className={INPUT_BASE}
                  value={repoPath}
                  onChange={(event) => setRepoPath(event.target.value)}
                  placeholder="/Users/you/gitops/platform"
                  aria-label="GitOps repo path"
                  required
                />
              </label>
              <label className="niuu-flex niuu-flex-col niuu-gap-1">
                <span className="niuu-text-xs niuu-text-text-muted">Manifests subdir</span>
                <input
                  className={INPUT_BASE}
                  value={manifestsSubdir}
                  onChange={(event) => setManifestsSubdir(event.target.value)}
                  placeholder="wardens"
                  aria-label="GitOps manifests subdir"
                />
              </label>
              <div className="niuu-flex niuu-flex-col niuu-gap-2 niuu-justify-end">
                <label className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
                  <input
                    type="checkbox"
                    checked={autoCommit}
                    onChange={(event) => setAutoCommit(event.target.checked)}
                    aria-label="Auto commit GitOps changes"
                  />
                  Auto commit
                </label>
                <label className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
                  <input
                    type="checkbox"
                    checked={autoPush}
                    onChange={(event) => setAutoPush(event.target.checked)}
                    aria-label="Auto push GitOps changes"
                  />
                  Auto push
                </label>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
        <label className="niuu-flex niuu-flex-col niuu-gap-1">
          <span className="niuu-text-xs niuu-text-text-muted">Mounts</span>
          <input
            className={INPUT_BASE}
            value={mounts}
            onChange={(event) => setMounts(event.target.value)}
            placeholder="local, shared"
            aria-label="Mounts"
          />
        </label>
        <label className="niuu-flex niuu-flex-col niuu-gap-1">
          <span className="niuu-text-xs niuu-text-text-muted">Write mount</span>
          <input
            className={INPUT_BASE}
            value={writeMount}
            onChange={(event) => setWriteMount(event.target.value)}
            placeholder="local"
            aria-label="Write mount"
          />
        </label>
      </div>

      <label className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
        <input
          type="checkbox"
          checked={autostart}
          onChange={(event) => setAutostart(event.target.checked)}
          aria-label="Autostart"
        />
        Mark for autostart after install
      </label>

      {errorMessage && (
        <div className="niuu-text-sm niuu-text-critical niuu-bg-critical-bg niuu-border niuu-border-critical-bo niuu-rounded-sm niuu-px-3 niuu-py-2">
          {errorMessage}
        </div>
      )}

      <div className="niuu-flex niuu-gap-2">
        <button type="submit" className={BTN_PRIMARY} disabled={isCreating}>
          {isCreating ? 'creating…' : 'Create warden'}
        </button>
        <button type="button" className={BTN_BASE} onClick={onCancel} disabled={isCreating}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface RavnCardProps {
  ravn: RavnBinding;
  onClick: () => void;
}

function RavnCard({ ravn, onClick }: RavnCardProps) {
  return (
    <article
      className="niuu-p-4 niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-bg-bg-secondary niuu-flex niuu-flex-col niuu-gap-3 niuu-cursor-pointer niuu-transition-colors hover:niuu-border-border focus-visible:niuu-outline focus-visible:niuu-outline-2 focus-visible:niuu-outline-brand focus-visible:niuu-outline-offset-2"
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick();
      }}
      data-testid="ravn-item"
      aria-label={`Warden ${ravn.ravnId}`}
    >
      <div className="niuu-flex niuu-items-center niuu-gap-3">
        <span
          className="niuu-inline-flex niuu-items-center niuu-justify-center niuu-font-mono niuu-text-sm niuu-font-bold niuu-text-text-secondary niuu-bg-bg-tertiary niuu-border niuu-border-border-subtle niuu-uppercase niuu-shrink-0"
          style={{ width: 36, height: 36, borderRadius: 'var(--radius-sm)' }}
          aria-hidden
        >
          {ravn.ravnId.charAt(0)}
          {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
        </span>
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-flex-1 niuu-min-w-0">
          <span className="niuu-font-mono niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-truncate">
            {ravn.ravnId}
          </span>
          <span
            className={`niuu-text-xs niuu-font-mono niuu-px-2 niuu-rounded-sm niuu-shrink-0 ${STATE_PILL[ravn.state]}`}
            data-testid="ravn-state"
          >
            {ravn.state}
          </span>
        </div>
      </div>

      <div className="niuu-flex niuu-gap-1">
        <Chip tone="muted">{ravn.role}</Chip>
      </div>

      <p
        className="niuu-text-xs niuu-text-text-secondary niuu-m-0 niuu-line-clamp-2"
        data-testid="ravn-bio"
      >
        {ravn.bio}
      </p>

      <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
        {ravn.mountNames.map((m) => (
          <Chip key={m} tone={m === ravn.writeMount ? 'brand' : 'muted'}>
            {m === ravn.writeMount ? `✎ ${m}` : m}
          </Chip>
        ))}
      </div>

      <div className="niuu-flex niuu-items-center niuu-gap-4 niuu-pt-2 niuu-border-t niuu-border-border-subtle niuu-text-xs niuu-font-mono">
        <span className="niuu-text-text-secondary">
          <strong className="niuu-text-text-primary">{ravn.pagesTouched}</strong> pages touched
        </span>
        {ravn.lastDream ? (
          <span className="niuu-text-text-muted" data-testid="ravn-dream">
            last dream {formatTimestamp(ravn.lastDream.timestamp)}
          </span>
        ) : (
          <span className="niuu-text-text-muted niuu-italic" data-testid="ravn-no-dream">
            no dream cycles yet
          </span>
        )}
      </div>

      {ravn.lastDream && (
        <div className="niuu-text-xs niuu-text-text-secondary">
          <strong className="niuu-text-text-primary">{ravn.lastDream.pagesUpdated}</strong> pages ·{' '}
          <strong className="niuu-text-text-primary">{ravn.lastDream.entitiesCreated}</strong>{' '}
          entities · {formatDuration(ravn.lastDream.durationMs)}
        </div>
      )}
    </article>
  );
}

interface RavnProfileProps {
  ravn: RavnBinding;
  warden: RavnWardenSummary;
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

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-6" data-testid="ravn-profile">
      <button
        type="button"
        className="niuu-self-start niuu-bg-transparent niuu-border-none niuu-text-text-muted niuu-text-sm niuu-cursor-pointer niuu-p-0 hover:niuu-text-text-secondary"
        onClick={onBack}
        aria-label="Back to wardens list"
      >
        ← Wardens
      </button>

      <div className="niuu-flex niuu-items-center niuu-gap-4 niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg">
        <span
          className="niuu-inline-flex niuu-items-center niuu-justify-center niuu-font-mono niuu-text-xl niuu-font-bold niuu-text-text-secondary niuu-bg-bg-tertiary niuu-border niuu-border-border-subtle niuu-uppercase niuu-shrink-0"
          style={{ width: 48, height: 48, borderRadius: 'var(--radius-sm)' }}
          aria-hidden
        >
          {ravn.ravnId.charAt(0)}
          {ravn.ravnId.charAt(ravn.ravnId.length - 1)}
        </span>
        <div className="niuu-flex niuu-flex-col niuu-gap-2 niuu-flex-1">
          <h2 className="niuu-m-0 niuu-text-xl niuu-font-mono">{warden.name}</h2>
          <div className="niuu-flex niuu-items-center niuu-gap-2">
            <Chip tone="muted">{ravn.role}</Chip>
            <span
              className={`niuu-text-xs niuu-font-mono niuu-px-2 niuu-rounded-sm ${STATE_PILL[ravn.state]}`}
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
              className="niuu-text-xs niuu-font-mono niuu-text-text-muted"
              data-testid="ravn-tools"
            >
              tools: {ravn.tools.join(' · ')}
            </span>
          )}
        </div>
      </div>

      <section className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg">
        <div className="niuu-flex niuu-items-center niuu-justify-between niuu-gap-3 niuu-mb-3">
          <div>
            <h4 className="niuu-m-0 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
              Lifecycle
            </h4>
            <p className="niuu-m-0 niuu-text-sm niuu-text-text-secondary">
              {lifecycleCopy(warden.deployment)}
            </p>
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
            <button type="button" className={BTN_BASE} onClick={onInstall} disabled={isInstalling}>
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

        <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-text-sm">
          <div>
            <div className="niuu-text-text-muted">deployment</div>
            <div className="niuu-font-mono niuu-text-text-primary">{deploymentLabel(warden.deployment)}</div>
          </div>
          <div>
            <div className="niuu-text-text-muted">persona</div>
            <div className="niuu-font-mono niuu-text-text-primary">{warden.persona}</div>
          </div>
          {deploymentEntries.length > 0 && (
            <div className="niuu-col-span-2">
              <div className="niuu-text-text-muted">deployment options</div>
              <div
                className="niuu-mt-2 niuu-grid niuu-grid-cols-2 niuu-gap-2 niuu-text-xs"
                data-testid="warden-deployment-config"
              >
                {deploymentEntries.map(([key, value]) => (
                  <div
                    key={key}
                    className="niuu-p-2 niuu-bg-bg-primary niuu-border niuu-border-border-subtle niuu-rounded-sm"
                  >
                    <div className="niuu-text-text-muted">{toTitleCase(key)}</div>
                    <div className="niuu-font-mono niuu-text-text-primary niuu-break-all">
                      {formatDeploymentValue(value)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {actionError && (
          <div className="niuu-mt-3 niuu-text-sm niuu-text-critical niuu-bg-critical-bg niuu-border niuu-border-critical-bo niuu-rounded-sm niuu-px-3 niuu-py-2">
            {actionError}
          </div>
        )}
      </section>

      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-4">
        <section
          className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-col-span-2"
          data-testid="warden-observed-placement"
        >
          <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-3 niuu-mb-3">
            <div>
              <h4 className="niuu-m-0 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
                Observed placement
              </h4>
              {observation?.detail && (
                <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-text-text-secondary">
                  {observation.detail}
                </p>
              )}
              {!observation?.detail && !isObserving && !observationError && (
                <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-text-text-secondary">
                  Live backend updates stream here over SSE while this profile is open.
                </p>
              )}
            </div>
            <div className="niuu-flex niuu-items-center niuu-gap-2">
              {isObserving && (
                <span className="niuu-text-xs niuu-text-text-muted">refreshing…</span>
              )}
              <span
                className={`niuu-text-xs niuu-font-mono niuu-px-2 niuu-rounded-sm ${
                  OBSERVATION_PILL[observation?.status ?? 'unknown']
                }`}
                data-testid="warden-observation-status"
              >
                {observation?.status ?? 'unknown'}
              </span>
            </div>
          </div>
          {observation?.source && (
            <div className="niuu-text-xs niuu-text-text-muted niuu-mb-2" data-testid="warden-observation-source">
              source: {observation.source}
              {observation.checkedAt ? ` · checked ${formatTimestamp(observation.checkedAt)}` : ''}
            </div>
          )}
          {observationError && (
            <div className="niuu-mb-3 niuu-text-sm niuu-text-critical niuu-bg-critical-bg niuu-border niuu-border-critical-bo niuu-rounded-sm niuu-px-3 niuu-py-2">
              {observationError}
            </div>
          )}
          <div className="niuu-grid niuu-grid-cols-2 niuu-gap-2 niuu-text-xs">
            {observation?.fields?.map((field) => (
              <div
                key={`observed-${field.label}`}
                className="niuu-p-2 niuu-bg-bg-primary niuu-border niuu-border-border-subtle niuu-rounded-sm"
              >
                <div className="niuu-text-text-muted">{field.label}</div>
                <div className="niuu-font-mono niuu-text-text-primary niuu-break-all">
                  {field.value}
                </div>
              </div>
            ))}
            {placementFields.map((field) => (
              <div
                key={field.label}
                className="niuu-p-2 niuu-bg-bg-primary niuu-border niuu-border-border-subtle niuu-rounded-sm"
              >
                <div className="niuu-text-text-muted">{field.label}</div>
                <div
                  className="niuu-font-mono niuu-text-text-primary niuu-break-all"
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

        <section className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg">
          <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
            Mount bindings
          </h4>
          <div className="niuu-flex niuu-flex-col niuu-gap-2">
            {ravn.mountNames.map((m) => (
              <div key={m} className="niuu-flex niuu-items-center niuu-gap-2">
                <Chip tone={m === ravn.writeMount ? 'brand' : 'muted'}>
                  {m === ravn.writeMount ? `✎ ${m}` : m}
                </Chip>
                {m === ravn.writeMount && (
                  <span className="niuu-text-xs niuu-text-text-muted">write mount</span>
                )}
              </div>
            ))}
          </div>
        </section>

        <section
          className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
          data-testid="ravn-expertise"
        >
          <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
            Areas of expertise
          </h4>
          {ravn.expertise.length > 0 ? (
            <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
              {ravn.expertise.map((e) => (
                <Chip key={e} tone="brand">
                  {e}
                </Chip>
              ))}
            </div>
          ) : (
            <p className="niuu-text-sm niuu-text-text-muted niuu-italic niuu-m-0">
              no expertise defined
            </p>
          )}
        </section>

        {ravn.lastDream ? (
          <section
            className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
            data-testid="ravn-dream"
          >
            <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
              Last dream
            </h4>
            <div className="niuu-flex niuu-flex-col niuu-gap-2">
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
                  className="niuu-flex niuu-justify-between niuu-items-baseline niuu-py-[3px] niuu-border-b niuu-border-border-subtle niuu-text-xs last:niuu-border-b-0"
                >
                  <span className="niuu-text-text-muted">{label}</span>
                  {bold ? (
                    <strong className="niuu-text-text-primary">{value}</strong>
                  ) : (
                    <span className="niuu-font-mono niuu-text-text-secondary">{value}</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        ) : (
          <section
            className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
            data-testid="ravn-no-dream"
          >
            <h4 className="niuu-m-0 niuu-mb-3 niuu-text-xs niuu-uppercase niuu-tracking-widest niuu-text-text-muted">
              Last dream
            </h4>
            <p className="niuu-text-sm niuu-text-text-muted niuu-italic niuu-m-0">
              no dream cycles yet
            </p>
          </section>
        )}
      </div>
    </div>
  );
}

export function RavnsPage() {
  const { data: wardens, isLoading, isError, error } = useWardenDirectory();
  const createWarden = useCreateWarden();
  const installWarden = useInstallWarden();
  const startWarden = useStartWarden();
  const stopWarden = useStopWarden();
  const uninstallWarden = useUninstallWarden();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const observedWardenQuery = useObservedWarden(selectedId);

  const ravns = (wardens ?? []).map(toRavnBinding);
  const selectedWarden = selectedId
    ? (wardens ?? []).find((warden) => warden.id === selectedId)
    : null;
  const profileWarden = observedWardenQuery.data ?? selectedWarden;
  const selectedRavn = profileWarden ? toRavnBinding(profileWarden) : null;

  async function handleCreate(draft: {
    name: string;
    persona: string;
    profile: string;
    deployment: DeploymentKind;
    deploymentKwargs: Record<string, unknown>;
    mounts: string[];
    writeMount: string;
    autostart: boolean;
  }) {
    setActionError(null);
    try {
      const created = await createWarden.mutateAsync({
        name: draft.name,
        persona: draft.persona,
        profile: draft.profile,
        deployment: draft.deployment,
        deploymentKwargs: draft.deploymentKwargs,
        mountNames: draft.mounts,
        writeMount: draft.writeMount || draft.mounts[0] || '',
        categoryScope: draft.mounts,
        autostart: draft.autostart,
        createdBy: 'mimir-ui',
      });
      setIsCreating(false);
      setSelectedId(created.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'warden create failed');
    }
  }

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
      <div className="niuu-p-6">
        <RavnProfile
          ravn={selectedRavn}
          warden={profileWarden}
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
    <div className="niuu-p-6">
      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4 niuu-mb-6">
        <div>
          <h2 className="niuu-m-0 niuu-mb-2 niuu-text-2xl niuu-font-semibold niuu-text-text-primary">
            Wardens
          </h2>
          <p className="niuu-m-0 niuu-text-sm niuu-text-text-secondary">
            Create, install, and start local or cluster-backed Ravn wardens from the Mímir control surface.
          </p>
        </div>
        <button
          type="button"
          className={BTN_PRIMARY}
          onClick={() => {
            setActionError(null);
            setIsCreating((current) => !current);
          }}
        >
          {isCreating ? 'Close form' : 'Create warden'}
        </button>
      </div>

      {isCreating && (
        <CreateWardenForm
          isCreating={createWarden.isPending}
          errorMessage={actionError}
          onCancel={() => {
            setActionError(null);
            setIsCreating(false);
          }}
          onSubmit={handleCreate}
        />
      )}

      {isLoading && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
          <StateDot state="processing" pulse />
          <span>loading wardens…</span>
        </div>
      )}

      {isError && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
          <StateDot state="failed" />
          <span>{error instanceof Error ? error.message : 'wardens load failed'}</span>
        </div>
      )}

      {!isLoading && !isError && ravns.length === 0 && (
        <p className="niuu-text-sm niuu-text-text-muted">No wardens found.</p>
      )}

      {ravns.length > 0 && (
        <div className="niuu-grid niuu-grid-cols-[repeat(auto-fill,minmax(280px,1fr))] niuu-gap-4">
          {ravns.map((ravn) => (
            <RavnCard key={ravn.ravnId} ravn={ravn} onClick={() => setSelectedId(ravn.ravnId)} />
          ))}
        </div>
      )}
    </div>
  );
}
