import { useRef, useState } from 'react';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import type { IBifrostService } from '@niuulabs/plugin-bifrost';
import { LoadingState, RepoSelect, type RepoRecord } from '@niuulabs/ui';
import type { IVolundrService } from '../ports/IVolundrService';
import {
  FORGE_STANDARDS,
  forgeErrorMessage,
  EFFORT_LABELS,
  hostDefaultFolder,
  selectedEffort,
  type ForgeStandardId,
} from './quickLaunchModel';
import { slugifySessionName, validateSessionName } from './launchWizardModel';
import {
  availableEngines,
  quickLaunchIntegrationIds,
  selectedEngineProvider,
} from './launchEngines';
import { useForgePreference } from './useForgePreference';
import './QuickLaunch.css';

type RepoCatalog = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repo: string): Promise<string[]>;
};
export function QuickLaunch({
  initialStandard = 'claude',
  onAdvanced,
  onCreated,
}: {
  initialStandard?: ForgeStandardId;
  onAdvanced: () => void;
  onCreated?: () => void;
}) {
  const volundr = useService<IVolundrService>('volundr');
  const bifrost = useService<IBifrostService>('bifrost');
  const repoCatalog = useService<RepoCatalog>('niuu.repos');
  const client = useQueryClient();
  const navigate = useNavigate();
  const hosts = useQuery({
    queryKey: ['volundr', 'targets'],
    queryFn: () => volundr.getTargets(),
  });
  const catalog = useQuery({
    queryKey: ['bifrost', 'model-catalog', 'quick-launch'],
    queryFn: () => bifrost.getModelCatalog(),
  });
  const definitions = useQuery({
    queryKey: ['volundr', 'session-definitions'],
    queryFn: () => volundr.getSessionDefinitions(),
  });
  const [standardId, setStandardId] = useState<ForgeStandardId>(initialStandard);
  const standard = FORGE_STANDARDS.find((s) => s.id === standardId)!;
  const [modelSelection, setModelSelection] = useState('');
  const modelId = standard.models.some((m) => m.id === modelSelection)
    ? modelSelection
    : standard.models[0].id;
  const model = catalog.data?.[modelId];
  const [preferredEffort, setPreferredEffort] = useForgePreference<string>(
    `launch.effort.${modelId}`,
    'xhigh',
  );
  const effort = selectedEffort(model, preferredEffort);
  const [preferredHost, setPreferredHost] = useForgePreference<string>('launch.host', '');
  const enabledHosts = hosts.data?.filter((h) => h.enabled) ?? [];
  const host =
    enabledHosts.find((h) => h.id === preferredHost) ??
    enabledHosts.find((h) => h.isDefault) ??
    enabledHosts[0];
  const [savedFolder, setSavedFolder] = useForgePreference<string>(
    `launch.folder.${host?.id ?? ''}`,
    hostDefaultFolder(host),
  );
  const [folders, setFolders] = useState<Record<string, string>>({});
  const folder = host ? (folders[host.id] ?? savedFolder) : '';
  // Local folders exist only where the Forge mounts them; a mini-mode host
  // defaults to one, a cluster to Git. A source the person picked stays. While
  // a newly chosen Forge answers, the form keeps its shape but cannot launch.
  const features = useQuery({
    queryKey: ['volundr', 'features', host?.id],
    queryFn: () => volundr.getFeatures(host?.id),
    enabled: Boolean(host),
    placeholderData: keepPreviousData,
  });
  const localMountsEnabled = Boolean(features.data?.localMountsEnabled);
  const [pickedSource, setPickedSource] = useState<'git' | 'local_mount' | null>(null);
  const sourceType =
    pickedSource ?? (features.data?.miniMode && localMountsEnabled ? 'local_mount' : 'git');
  const [repo, setRepo] = useState('');
  const [branch, setBranch] = useState('');
  const repos = useQuery({
    queryKey: ['niuu', 'launch-repos'],
    queryFn: () => repoCatalog.getRepos(),
    enabled: sourceType === 'git',
  });
  const branches = useQuery({
    queryKey: ['niuu', 'launch-branches', repo],
    queryFn: () => repoCatalog.getBranches(repo),
    enabled: sourceType === 'git' && Boolean(repo),
  });
  // The session carries the connected AI account that powers this standard and
  // the Git account that lists the repository, as the advanced launch does.
  const integrations = useQuery({
    queryKey: ['volundr', 'integrations'],
    queryFn: () => volundr.getIntegrations(),
  });
  const integrationCatalog = useQuery({
    queryKey: ['volundr', 'integration-catalog'],
    queryFn: () => volundr.getIntegrationCatalog(),
  });
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [launching, setLaunching] = useState(false);
  const submitting = useRef(false);
  const [launchError, setLaunchError] = useState('');
  const loadError =
    hosts.error ??
    catalog.error ??
    definitions.error ??
    features.error ??
    integrations.error ??
    integrationCatalog.error;
  const available = model?.enabled && definitions.data?.some((d) => d.key === standard.definition);
  const nameError = validateSessionName(name.trim());
  const validSource =
    sourceType === 'local_mount'
      ? localMountsEnabled && folder.trim().startsWith('/')
      : Boolean(repo.trim() && branch.trim());
  const canLaunch = Boolean(
    host &&
    available &&
    validSource &&
    features.data &&
    !features.isPlaceholderData &&
    !nameError &&
    !loadError &&
    !launching,
  );

  async function launch(event: React.FormEvent) {
    event.preventDefault();
    if (!canLaunch || !host || submitting.current) return;
    submitting.current = true;
    setLaunching(true);
    setLaunchError('');
    try {
      const autoName =
        slugifySessionName(
          (sourceType === 'local_mount' ? folder : repo)
            .split('/')
            .filter(Boolean)
            .at(-1)
            ?.replace(/\.git$/, '') ?? 'forge-session',
        ) || 'forge-session';
      const local = sourceType === 'local_mount';
      const engine = availableEngines(
        definitions.data ?? [],
        integrations.data ?? [],
        integrationCatalog.data ?? [],
      ).find((option) => option.definition.key === standard.definition);
      const session = await volundr.startSession({
        name: name.trim() || autoName,
        definition: standard.definition,
        model: modelId,
        instanceId: host.id,
        integrationIds: quickLaunchIntegrationIds({
          provider: selectedEngineProvider(engine, []),
          integrations: integrations.data ?? [],
          repos: repos.data ?? [],
          repoUrl: repo.trim(),
          local,
        }),
        source:
          sourceType === 'local_mount'
            ? {
                type: 'local_mount',
                local_path: folder.trim(),
                paths: [{ host_path: folder.trim(), mount_path: '/workspace', read_only: false }],
              }
            : { type: 'git', repo: repo.trim(), branch: branch.trim() },
        ...(effort ? { workloadConfig: { reasoningEffort: effort } } : {}),
        ...(prompt.trim() ? { initialPrompt: prompt.trim() } : {}),
      });
      setPreferredHost(host.id);
      if (sourceType === 'local_mount') setSavedFolder(folder.trim());
      await Promise.all([
        client.invalidateQueries({ queryKey: ['volundr', 'sessions'] }),
        client.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        client.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
      ]);
      onCreated?.();
      await navigate({ to: '/volundr/sessions/$sessionId', params: { sessionId: session.id } });
    } catch (e) {
      setLaunchError(forgeErrorMessage(e));
    } finally {
      submitting.current = false;
      setLaunching(false);
    }
  }
  return (
    <form
      className="vol-quick"
      onSubmit={(event) => void launch(event)}
      data-testid="quick-launch-form"
    >
      <fieldset
        disabled={launching}
        style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
        className="vol-quick"
      >
        <div className="vol-quick__standards" aria-label="Launch standard">
          {FORGE_STANDARDS.map((s) => (
            <button
              key={s.id}
              type="button"
              className="vol-quick__standard"
              aria-pressed={s.id === standardId}
              onClick={() => {
                setStandardId(s.id);
                setModelSelection('');
                setLaunchError('');
              }}
            >
              <strong>{s.name}</strong>
              <span>{s.harness}</span>
              <span>{s.models[0].name} by default</span>
            </button>
          ))}
        </div>
        {(hosts.isLoading || catalog.isLoading || definitions.isLoading) && (
          <LoadingState label="Loading launch options…" />
        )}
        {loadError && (
          <p role="alert">Could not load launch options: {forgeErrorMessage(loadError)}</p>
        )}
        <section className="vol-quick__section">
          <div className="vol-quick__actions">
            <h2>Workspace</h2>
            <a href="/guild" className="vol-quick__link">
              Manage environments in Guild
            </a>
          </div>
          <div className="vol-quick__row">
            <label>
              Forge
              <select
                value={host?.id ?? ''}
                onChange={(e) => {
                  setPreferredHost(e.target.value);
                  // A local folder is re-derived from the new Forge, which may not mount one.
                  setPickedSource((current) => (current === 'local_mount' ? null : current));
                }}
                aria-label="Forge"
              >
                {!host && <option value="">Select a Forge</option>}
                {enabledHosts.map((h) => (
                  <option key={h.id} value={h.id}>
                    {h.name} · {h.baseUrl}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Workspace source
              <select
                value={sourceType}
                onChange={(e) => setPickedSource(e.target.value as 'git' | 'local_mount')}
              >
                {localMountsEnabled && <option value="local_mount">Local mount</option>}
                <option value="git">Git repository</option>
              </select>
            </label>
          </div>
          {!hosts.isLoading && enabledHosts.length === 0 && !hosts.error && (
            <p role="alert">Add an enabled Forge host to launch a session.</p>
          )}
          {sourceType === 'local_mount' ? (
            <label>
              Working folder
              <input
                aria-label="Working folder"
                value={folder}
                onChange={(e) => host && setFolders({ ...folders, [host.id]: e.target.value })}
                placeholder="Absolute folder path on this Forge"
              />
              <small>
                Existing folder on {host?.name ?? 'the selected Forge'}. Remembered separately for
                each host.
              </small>
            </label>
          ) : (
            <>
              {repos.error && <p role="alert">{repos.error.message}</p>}
              <label>
                Repository
                <RepoSelect
                  repos={repos.data ?? []}
                  value={repo}
                  onChange={(value) => {
                    setRepo(value);
                    setBranch(repos.data?.find((r) => r.cloneUrl === value)?.defaultBranch ?? '');
                  }}
                  placeholder="Select repository"
                />
              </label>
              <label>
                Branch
                <input
                  list="quick-launch-branches"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="Branch name"
                />
                <datalist id="quick-launch-branches">
                  {branches.data?.map((b) => (
                    <option key={b} value={b} />
                  ))}
                </datalist>
              </label>
              {branches.error && (
                <p role="alert">Could not load branches: {branches.error.message}</p>
              )}
            </>
          )}
        </section>
        <section className="vol-quick__section">
          <h2>{standard.name} standard</h2>
          <div className="vol-quick__row">
            <label>
              Model
              <select
                aria-label="Model"
                value={modelId}
                onChange={(e) => setModelSelection(e.target.value)}
              >
                {standard.models.map((m) => (
                  <option key={m.id} value={m.id} disabled={!catalog.data?.[m.id]?.enabled}>
                    {m.name}
                    {catalog.data && !catalog.data[m.id]?.enabled ? ' · unavailable' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Effort
              <select
                aria-label="Effort"
                value={effort}
                disabled={!model?.effortLevels?.length}
                onChange={(e) => setPreferredEffort(e.target.value)}
              >
                {!effort && <option value="">Host default</option>}
                {model?.effortLevels?.map((level) => (
                  <option key={level} value={level}>
                    {EFFORT_LABELS[level] ?? level}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <small>
            {model?.effortNote ||
              'Extra High is preferred when supported. Your effort choice is remembered for this model.'}
          </small>
          {catalog.data && definitions.data && !available && (
            <p role="alert">
              This standard is unavailable in the connected Niuu catalogue. Enable its model and
              runtime before launching.
            </p>
          )}
          <p>Host defaults apply. Resources, MCP servers and rules are optional.</p>
        </section>
        <details>
          <summary>Session details · optional</summary>
          <div className="vol-quick">
            <label>
              Session name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Derived from your workspace"
              />
              {nameError && <small role="alert">{nameError}</small>}
            </label>
            <label>
              Initial prompt
              <textarea
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="What would you like to work on?"
              />
            </label>
            <small>
              Tracker issues, credentials, MCP servers and custom resources are available in
              Advanced launch.
            </small>
          </div>
        </details>
      </fieldset>
      {launchError && <p role="alert">{launchError}</p>}
      <div className="vol-quick__actions vol-quick__actions--launch">
        <button
          type="button"
          className="vol-quick__button"
          disabled={launching}
          onClick={onAdvanced}
        >
          Advanced launch
        </button>
        <button
          type="submit"
          className="vol-quick__button vol-quick__primary"
          disabled={!canLaunch}
        >
          {launching ? 'Starting session…' : `Launch ${standard.name}`}
        </button>
      </div>
    </form>
  );
}
