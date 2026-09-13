import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import {
  BranchSelect,
  Dialog,
  DialogContent,
  Field,
  Input,
  RepoSelect,
  Textarea,
  type RepoRecord,
} from '@niuulabs/ui';
import type { IVolundrService } from '../ports/IVolundrService';
import type { SessionSource } from '../models/volundr.model';
import { definitionToTaskType, slugifySessionName, validateSessionName } from './launchWizardModel';
import { EngineSelect } from './EngineSelect';
import { LinkedText } from './LinkedText';
import { errorText } from './errorText';
import {
  availableEngines,
  quickLaunchIntegrationIds,
  selectedEngineProvider,
} from './launchEngines';
import { LaunchWizard } from './LaunchWizard';
import { useFeatures } from './useFeatures';

/** The shared repository catalog (`niuu.repos`), the same one the advanced launch reads. */
type RepoCatalog = { getRepos(): Promise<RepoRecord[]> };

export interface QuickLaunchProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialLaunchSpecRef?: string;
}

const PRIMARY_BTN =
  'niuu:rounded-md niuu:border niuu:border-brand niuu:bg-brand niuu:px-4 niuu:py-2 niuu:text-xs niuu:font-mono niuu:text-bg-primary niuu:cursor-pointer niuu:disabled:opacity-50 niuu:disabled:cursor-not-allowed';
const CANCEL_BTN =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-4 niuu:py-2 niuu:text-xs niuu:font-mono niuu:text-text-primary niuu:hover:bg-bg-tertiary';

/** Compact launch for local and remote Forge hosts; advanced configuration remains available. */
export function QuickLaunch({ open, onOpenChange, initialLaunchSpecRef }: QuickLaunchProps) {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const definitionsQuery = useQuery({
    queryKey: ['volundr', 'session-definitions'],
    queryFn: () => volundr.getSessionDefinitions(),
    enabled: open,
  });

  const targetsQuery = useQuery({
    queryKey: ['volundr', 'targets'],
    queryFn: () => volundr.getTargets(),
    enabled: open,
  });
  // Only engines a connected AI provider powers are offered; the join runs
  // through the catalog, which says which model vendor each provider unlocks.
  const integrationsQuery = useQuery({
    queryKey: ['volundr', 'integrations'],
    queryFn: () => volundr.getIntegrations(),
    enabled: open,
  });
  const catalogQuery = useQuery({
    queryKey: ['volundr', 'integration-catalog'],
    queryFn: () => volundr.getIntegrationCatalog(),
    enabled: open,
  });
  const definitions = definitionsQuery.data ?? [];
  const engines = useMemo(
    () =>
      availableEngines(
        definitionsQuery.data ?? [],
        integrationsQuery.data ?? [],
        catalogQuery.data ?? [],
      ),
    [definitionsQuery.data, integrationsQuery.data, catalogQuery.data],
  );
  const providerError = integrationsQuery.error ?? catalogQuery.error ?? null;
  const providersLoading = integrationsQuery.isPending || catalogQuery.isPending;
  const targets = (targetsQuery.data ?? []).filter((target) => target.enabled);
  const [targetId, setTargetId] = useState('');
  const [sourceType, setSourceType] = useState<'git' | 'local_mount' | null>(null);
  const [branch, setBranch] = useState('');
  const [advanced, setAdvanced] = useState(false);
  const selectedTarget =
    targetId || targets.find((target) => target.isDefault)?.id || targets[0]?.id;
  const features = useFeatures(open && targetsQuery.isSuccess, selectedTarget);
  const local =
    (sourceType ??
      (features.data?.miniMode && features.data.localMountsEnabled ? 'local_mount' : 'git')) ===
    'local_mount';
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('');
  // The same repository list the advanced launch offers: every repository
  // the person's connected Git accounts can reach.
  const repoCatalog = useService<RepoCatalog>('niuu.repos');
  const reposQuery = useQuery({
    queryKey: ['volundr', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
    enabled: open && !local,
  });
  const repos: RepoRecord[] = reposQuery.data ?? [];
  const [customRepo, setCustomRepo] = useState(false);
  const selectedRepo = repos.find((repo) => repo.cloneUrl === folder);
  const pickFromList = !local && repos.length > 0 && !customRepo && (!folder || !!selectedRepo);
  const [definitionKey, setDefinitionKey] = useState('skuldClaude');
  const [prompt, setPrompt] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedEngine =
    engines.find((engine) => engine.definition.key === definitionKey) ?? engines[0];
  const selectedDef = selectedEngine?.definition;
  // Which account runs the engine when several could; the first otherwise.
  const [providerId, setProviderId] = useState('');
  const selectedProvider = selectedEngineProvider(selectedEngine, providerId ? [providerId] : []);

  // Auto-derive the session name from the folder's last path segment when blank.
  const effectiveName = useMemo(() => {
    const explicit = slugifySessionName(name);
    if (explicit) return explicit;
    const lastSegment = (folder || '').split('/').filter(Boolean).at(-1) ?? '';
    const fromFolder = slugifySessionName(lastSegment.replace(/\.git$/, '').replace(/^~/, 'home'));
    return fromFolder || 'forge-session';
  }, [name, folder]);

  const nameError = validateSessionName(effectiveName);
  const loadError = definitionsQuery.error ?? features.error ?? targetsQuery.error;
  const loading =
    definitionsQuery.isPending || features.isPending || targetsQuery.isPending || providersLoading;
  const canCreate =
    Boolean(folder.trim()) &&
    Boolean(selectedDef) &&
    !nameError &&
    !creating &&
    !loading &&
    !loadError &&
    !providerError &&
    (!local || Boolean(features.data?.localMountsEnabled));

  async function handleCreate() {
    if (!canCreate) return;
    setError(null);
    setCreating(true);
    try {
      const def = selectedDef;
      const path = folder.trim();
      const source: SessionSource = local
        ? {
            type: 'local_mount',
            local_path: path,
            paths: [{ host_path: path, mount_path: '/workspace', read_only: false }],
          }
        : { type: 'git', repo: path, branch: branch.trim() };
      const session = await volundr.startSession({
        name: effectiveName,
        source,
        instanceId: selectedTarget,
        model: def?.defaultModel ?? '',
        definition: def?.key,
        taskType: def ? definitionToTaskType(def.key) : undefined,
        initialPrompt: prompt.trim() || undefined,
        // Exactly one AI credential (the chosen account), the Git account
        // that listed the repository, and the rest of the person's
        // integrations; never every AI account at once.
        integrationIds: quickLaunchIntegrationIds({
          provider: selectedProvider,
          integrations: integrationsQuery.data ?? [],
          repos,
          repoUrl: path,
          local,
        }),
        terminalRestricted: false,
        workloadConfig: {},
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      ]);
      onOpenChange(false);
      void navigate({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: session.id },
      });
    } catch (e) {
      setError(errorText(e, 'Failed to create session'));
    } finally {
      setCreating(false);
    }
  }

  if (advanced || initialLaunchSpecRef) {
    return (
      <LaunchWizard
        open={open}
        initialLaunchSpecRef={initialLaunchSpecRef}
        initialForm={
          advanced
            ? {
                sourcetype: local ? 'local_mount' : 'git',
                repo: local ? '' : folder.trim(),
                mountPath: local ? folder.trim() : '',
                branch: branch.trim(),
                sessionName: effectiveName,
                instanceId: selectedTarget ?? '',
                initialPrompt: prompt,
                definition: selectedDef?.key ?? '',
                model: selectedDef?.defaultModel ?? '',
              }
            : undefined
        }
        onOpenChange={(next) => {
          if (!next) setAdvanced(false);
          onOpenChange(next);
        }}
      />
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New session">
        <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="quick-launch">
          <Field label="Source">
            <select
              className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
              aria-label="Source type"
              value={local ? 'local_mount' : 'git'}
              onChange={(event) => {
                setSourceType(event.target.value as 'git' | 'local_mount');
                setFolder('');
              }}
            >
              <option value="git">Git repository</option>
              {features.data?.localMountsEnabled && (
                <option value="local_mount">Local folder</option>
              )}
            </select>
          </Field>
          <Field
            label={local ? 'Folder' : 'Repository'}
            hint={
              local
                ? 'Absolute path on the Forge host; runs in place'
                : reposQuery.error
                  ? `Could not list your repositories (${reposQuery.error.message}); paste a clone URL`
                  : pickFromList
                    ? 'From your connected Git accounts'
                    : 'Repository clone URL'
            }
          >
            {pickFromList ? (
              <RepoSelect
                repos={repos}
                value={folder}
                onChange={(value: string) => {
                  setFolder(value);
                  setBranch(repos.find((repo) => repo.cloneUrl === value)?.defaultBranch ?? '');
                }}
                placeholder="Select repository"
                testId="quick-launch-repo"
              />
            ) : (
              <Input
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                placeholder={
                  local ? '/path/to/checkout' : 'https://github.com/owner/repository.git'
                }
                data-testid="quick-launch-folder"
              />
            )}
            {!local && repos.length > 0 ? (
              <button
                type="button"
                className="niuu:mt-1 niuu:self-start niuu:text-xs niuu:text-text-secondary niuu:hover:text-text-primary"
                onClick={() => {
                  setCustomRepo((value) => !value);
                  setFolder('');
                  setBranch('');
                }}
                data-testid="quick-launch-repo-toggle"
              >
                {pickFromList ? 'Paste a clone URL instead' : 'Choose from your repositories'}
              </button>
            ) : null}
          </Field>

          {!local && (
            <Field
              label="Branch"
              hint={
                selectedRepo?.branches.length ? undefined : 'Optional — uses the repository default'
              }
            >
              {selectedRepo?.branches.length ? (
                <BranchSelect
                  repos={repos}
                  selectedRepos={folder}
                  value={branch}
                  onChange={(value: string) => setBranch(value)}
                  placeholder="Select branch"
                  testId="quick-launch-branch"
                />
              ) : (
                <Input
                  aria-label="Branch"
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                />
              )}
            </Field>
          )}
          {targets.length > 1 && (
            <Field label="Forge">
              <select
                className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
                aria-label="Forge target"
                value={selectedTarget}
                onChange={(event) => {
                  setTargetId(event.target.value);
                  // A source the person chose stays; a derived one is derived again
                  // from the new target's capabilities (which may not allow local
                  // folders), never pinned by whatever the mode was mid-load.
                  setSourceType((current) => (current === 'local_mount' ? null : current));
                  if (local) setFolder('');
                }}
              >
                {targets.map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field
            label="Name"
            hint={
              local
                ? 'Optional — derived from the folder if left blank'
                : 'Optional — derived from the repository if left blank'
            }
            error={nameError ?? undefined}
          >
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={effectiveName}
              data-testid="quick-launch-name"
            />
          </Field>

          {definitions.length > 0 ? (
            <EngineSelect
              engines={engines}
              value={selectedDef?.key ?? ''}
              onChange={setDefinitionKey}
              selectedIntegrationIds={providerId ? [providerId] : []}
              onProviderChange={setProviderId}
              loading={providersLoading}
              error={providerError}
              testId="quick-launch-engine"
            />
          ) : null}

          <Field label="First instruction" hint="Optional — what should the agent start on?">
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="e.g. Fix the failing auth tests"
              data-testid="quick-launch-prompt"
            />
          </Field>

          {loading && <p role="status">Loading launch options…</p>}
          {loadError && <p role="alert">{loadError.message}</p>}
          {!loading && !loadError && definitions.length === 0 && (
            <p role="status">No session engines are configured.</p>
          )}
          {error ? (
            <p
              role="alert"
              className="niuu:text-xs niuu:text-danger"
              data-testid="quick-launch-error"
            >
              <LinkedText text={error} />
            </p>
          ) : null}

          <div className="niuu:flex niuu:justify-end niuu:gap-2">
            <button type="button" onClick={() => setAdvanced(true)} className={CANCEL_BTN}>
              Advanced launch
            </button>
            <button type="button" onClick={() => onOpenChange(false)} className={CANCEL_BTN}>
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={!canCreate}
              data-testid="quick-launch-go"
              className={PRIMARY_BTN}
            >
              {creating ? 'Creating…' : 'Go →'}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
