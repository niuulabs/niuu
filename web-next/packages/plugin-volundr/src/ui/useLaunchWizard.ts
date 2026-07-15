import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { BifrostModel, IBifrostService } from '@niuulabs/plugin-bifrost';
import type { IPersonaCatalog, PersonaSummary } from '@niuulabs/domain';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import type { RepoRecord } from '@niuulabs/ui';
import type { IVolundrService } from '../ports/IVolundrService';
import type {
  ClusterResourceInfo,
  McpServerConfig,
  SessionDefinition,
  IntegrationConnection,
  VolundrTarget,
  StoredCredential,
  TrackerIssue,
  VolundrLaunchSpec,
  VolundrWorkspace,
} from '../models/volundr.model';

import {
  buildPresetComparisonPayload,
  buildPresetPayload,
  buildPresetRuntimePayload,
  buildResourceConfig,
  buildSessionSource,
  buildWorkloadConfig,
  deriveSessionName,
  definitionToTaskType,
  filterModelsForDefinition,
  FALLBACK_SESSION_DEFINITIONS,
  getMatchingTargets,
  getResourceErrors,
  getTargetTagOptions,
  hasPresetBackedRuntime,
  launchSpecRef,
  normalizeDefinitionKey,
  pickDefaultModelForDefinition,
  validateSessionName,
  type RuntimeModelDescriptor,
  type WizardForm,
  type WizardStep,
} from './launchWizardModel';

import { BOOT_STEPS, STEPS, type LaunchWizardProps } from './LaunchWizardSteps';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

// ---------------------------------------------------------------------------
// LaunchWizard
// ---------------------------------------------------------------------------

/** 4-step modal wizard for launching new Volundr sessions. */
export function useLaunchWizard({ open, initialLaunchSpecRef }: LaunchWizardProps) {
  const volundr = useService<IVolundrService>('volundr');
  const bifrost = useService<IBifrostService>('bifrost');
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const personaCatalog = useOptionalService<IPersonaCatalog>('ravn.personas');
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [repos, setRepos] = useState<RepoRecord[]>([]);
  const [manualBranches, setManualBranches] = useState<string[]>([]);
  const [models, setModels] = useState<Record<string, RuntimeModelDescriptor>>({});
  const [workspaces, setWorkspaces] = useState<VolundrWorkspace[]>([]);
  const [credentials, setCredentials] = useState<StoredCredential[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationConnection[]>([]);
  const [clusterResources, setClusterResources] = useState<ClusterResourceInfo | null>(null);
  const [presets, setPresets] = useState<VolundrLaunchSpec[]>([]);
  const [targets, setTargets] = useState<VolundrTarget[]>([]);
  const [availableMcpServers, setAvailableMcpServers] = useState<McpServerConfig[]>([]);
  const [sessionDefinitions, setSessionDefinitions] = useState<SessionDefinition[]>([]);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [trackerResults, setTrackerResults] = useState<TrackerIssue[]>([]);
  const [trackerLoading, setTrackerLoading] = useState(false);

  const [step, setStep] = useState<WizardStep>('source');
  const [form, setForm] = useState<WizardForm>(() => ({
    presetId: initialLaunchSpecRef ?? '',
    sourcetype: 'git',
    repo: '',
    branch: '',
    workspaceId: '',
    mountPath: '~/code/niuu',
    sessionName: '',
    personaName: '',
    workloadConfig: {},
    systemPrompt: '',
    initialPrompt: '',
    trackerQuery: '',
    trackerIssue: null,
    selectedCredentials: [],
    selectedIntegrations: [],
    mcpServers: [],
    envVars: [],
    setupScripts: [],
    definition: 'skuldClaude',
    model: 'sonnet-primary',
    cpu: '2',
    mem: '8Gi',
    gpu: '0',
    cluster: '',
    instanceId: '',
    targetMode: 'instance',
    targetTags: [],
    targetMatch: 'all',
    yamlMode: false,
    yamlContent: '',
  }));
  const [bootStep, setBootStep] = useState(0);
  const [bootProgress, setBootProgress] = useState(0);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [createdSessionId, setCreatedSessionId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;

    let cancelled = false;

    void Promise.all([
      repoCatalog.getRepos().catch(() => []),
      bifrost.getModelCatalog().catch((): Record<string, BifrostModel> => ({})),
      Promise.all([
        volundr.listWorkspaces('archived').catch(() => []),
        volundr.listWorkspaces('active').catch(() => []),
      ]).then(([archived, active]) => [...archived, ...active]),
      volundr.getCredentials().catch(() => []),
      volundr.getIntegrations().catch(() => []),
      volundr.getClusterResources().catch(() => null),
      volundr.getLaunchSpecs().catch(() => []),
      volundr.getTargets().catch(() => []),
      volundr.getAvailableMcpServers().catch(() => []),
      volundr.getSessionDefinitions().catch(() => FALLBACK_SESSION_DEFINITIONS),
      personaCatalog?.listPersonas().catch(() => []) ?? Promise.resolve([]),
    ]).then(
      ([
        nextRepos,
        nextModels,
        nextWorkspaces,
        nextCredentials,
        nextIntegrations,
        nextClusterResources,
        nextPresets,
        nextTargets,
        nextMcpServers,
        nextSessionDefinitions,
        nextPersonas,
      ]) => {
        if (cancelled) return;
        setRepos(nextRepos);
        setModels(nextModels);
        setWorkspaces(nextWorkspaces);
        setCredentials(nextCredentials);
        setIntegrations(nextIntegrations);
        setClusterResources(nextClusterResources);
        setPresets(nextPresets);
        setTargets(nextTargets);
        setAvailableMcpServers(nextMcpServers);
        setSessionDefinitions(
          nextSessionDefinitions.length > 0 ? nextSessionDefinitions : FALLBACK_SESSION_DEFINITIONS,
        );
        setPersonas([...nextPersonas].sort((left, right) => left.name.localeCompare(right.name)));
      },
    );

    return () => {
      cancelled = true;
    };
  }, [bifrost, open, personaCatalog, repoCatalog, volundr]);

  useEffect(() => {
    if (!open || form.sourcetype !== 'git' || !form.repo.trim()) {
      queueMicrotask(() => {
        setManualBranches([]);
      });
      return;
    }

    const matchingRepo = repos.find((repo) => repo.cloneUrl === form.repo);
    if (matchingRepo?.branches.length) {
      queueMicrotask(() => {
        setManualBranches([]);
      });
      return;
    }

    let cancelled = false;
    void repoCatalog
      .getBranches(form.repo)
      .then((branches) => {
        if (!cancelled) setManualBranches(branches);
      })
      .catch(() => {
        if (!cancelled) setManualBranches([]);
      });

    return () => {
      cancelled = true;
    };
  }, [form.repo, form.sourcetype, open, repoCatalog, repos]);

  useEffect(() => {
    queueMicrotask(() => {
      setForm((current) => {
        let changed = false;
        const next = { ...current };

        if (repos.length > 0 && current.sourcetype === 'git') {
          const matchingRepo = repos.find((repo) => repo.cloneUrl === current.repo);
          if (!matchingRepo) {
            next.repo = repos[0]!.cloneUrl;
            next.branch = repos[0]!.defaultBranch;
            next.workspaceId = '';
            changed = true;
          } else if (!current.branch.trim()) {
            next.branch = matchingRepo.defaultBranch;
            changed = true;
          }
        }

        const compatibleModels = filterModelsForDefinition(
          models,
          current.definition,
          sessionDefinitions,
        );
        if (Object.keys(compatibleModels).length > 0 && !compatibleModels[current.model]) {
          next.model = pickDefaultModelForDefinition(
            models,
            current.definition,
            sessionDefinitions,
          );
          changed = true;
        }

        if (targets.length > 0) {
          const matchingTarget = targets.find((target) => target.id === current.instanceId);
          if (!matchingTarget) {
            next.instanceId = targets.find((target) => target.isDefault)?.id ?? targets[0]!.id;
            changed = true;
          }
          if (current.targetMode === 'tags' && getTargetTagOptions(targets).length === 0) {
            next.targetMode = 'instance';
            changed = true;
          }
        }

        return changed ? next : current;
      });
    });
  }, [repos, models, sessionDefinitions, targets]);

  useEffect(() => {
    const query = form.trackerQuery.trim();
    if (!open || query.length < 2 || form.trackerIssue?.identifier === query) {
      queueMicrotask(() => {
        setTrackerResults([]);
        setTrackerLoading(false);
      });
      return;
    }

    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) setTrackerLoading(true);
    });
    const timeout = window.setTimeout(() => {
      void volundr
        .searchTrackerIssues(query)
        .then((results) => {
          if (!cancelled) setTrackerResults(results);
        })
        .catch(() => {
          if (!cancelled) setTrackerResults([]);
        })
        .finally(() => {
          if (!cancelled) setTrackerLoading(false);
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [form.trackerQuery, form.trackerIssue, open, volundr]);

  const handleApplyPreset = useCallback(
    (ref: string) => {
      if (!ref) {
        setForm((current) => ({ ...current, presetId: '' }));
        return;
      }

      const preset = presets.find((item) => launchSpecRef(item) === ref);
      if (!preset) return;

      setForm((current) => ({
        ...current,
        presetId: ref,
        definition: normalizeDefinitionKey(preset.workloadType || `skuld-${preset.cliTool}`),
        model: preset.model ?? current.model,
        systemPrompt: preset.systemPrompt ?? '',
        personaName:
          typeof preset.workloadConfig.persona === 'string' ? preset.workloadConfig.persona : '',
        workloadConfig: { ...preset.workloadConfig },
        selectedCredentials: [...preset.envSecretRefs],
        selectedIntegrations: [...preset.integrationIds],
        mcpServers: [...preset.mcpServers],
        envVars: Object.entries(preset.envVars).map(([key, value]) => ({ key, value })),
        setupScripts: [...preset.setupScripts],
        cpu: preset.resourceConfig.cpu ?? current.cpu,
        mem: preset.resourceConfig.memory ?? current.mem,
        gpu: preset.resourceConfig.gpu ?? current.gpu,
        sourcetype:
          preset.source?.type === 'local_mount'
            ? 'local_mount'
            : preset.source?.type === 'git'
              ? 'git'
              : current.sourcetype,
        repo: preset.source?.type === 'git' ? preset.source.repo : current.repo,
        branch: preset.source?.type === 'git' ? preset.source.branch : current.branch,
        mountPath:
          preset.source?.type === 'local_mount'
            ? (preset.source.local_path ?? preset.source.paths[0]?.host_path ?? current.mountPath)
            : current.mountPath,
        yamlMode: false,
        yamlContent: '',
      }));
    },
    [presets],
  );

  useEffect(() => {
    if (!open || !initialLaunchSpecRef || presets.length === 0) return;
    if (presets.some((preset) => launchSpecRef(preset) === initialLaunchSpecRef)) {
      queueMicrotask(() => {
        handleApplyPreset(initialLaunchSpecRef);
      });
    }
  }, [handleApplyPreset, initialLaunchSpecRef, open, presets]);

  const handleSavePreset = useCallback(
    async (name: string) => {
      const saved = await volundr.saveLaunchSpec(buildPresetPayload(form, name));
      setPresets((current) => {
        const next = current.filter((preset) => preset.id !== saved.id);
        return [...next, saved];
      });
      setForm((current) => ({ ...current, presetId: launchSpecRef(saved) }));
    },
    [form, volundr],
  );

  // Boot animation
  useEffect(() => {
    if (step !== 'booting') return;
    let cancelled = false;
    let i = 0;
    const total = BOOT_STEPS.length;
    const tick = () => {
      if (cancelled) return;
      i++;
      setBootStep((s) => Math.min(s + 1, total - 1));
      setBootProgress((p) => Math.min(1, p + 1 / total));
      if (i < total) setTimeout(tick, 900);
    };
    const timer = setTimeout(tick, 600);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [step]);

  const update = useCallback(
    (patch: Partial<WizardForm>) =>
      setForm((current) => {
        const next = { ...current, ...patch };

        if (patch.sourcetype === 'git' && !next.repo && repos.length > 0) {
          next.repo = repos[0]!.cloneUrl;
          next.branch = repos[0]!.defaultBranch;
        }

        if (patch.repo && !Object.prototype.hasOwnProperty.call(patch, 'branch')) {
          const repo = repos.find((item) => item.cloneUrl === patch.repo);
          if (repo) {
            next.branch = repo.defaultBranch;
          }
        }

        return next;
      }),
    [repos],
  );

  const stepIdx = STEPS.indexOf(step as (typeof STEPS)[number]);
  const canGoBack = stepIdx > 0 && step !== 'booting';
  const isLastStep = step === 'confirm';
  const effectiveSessionName = deriveSessionName(form);
  const sessionNameError = validateSessionName(effectiveSessionName);
  const resourceErrors = getResourceErrors(form, clusterResources);
  const matchingTargets = getMatchingTargets(targets, form.targetTags, form.targetMatch);
  const targetRoutingError =
    form.targetMode === 'tags' && form.targetTags.length === 0
      ? 'Select at least one Forge tag.'
      : form.targetMode === 'tags' && matchingTargets.length === 0
        ? 'No enabled Forge target matches the selected tags.'
        : null;
  const sourceReady =
    form.sourcetype === 'blank' ||
    (form.sourcetype === 'git'
      ? Boolean(form.repo.trim()) && Boolean(form.branch.trim())
      : Boolean(form.mountPath.trim()));
  const canLaunch =
    Boolean(form.model.trim()) &&
    sourceReady &&
    !sessionNameError &&
    !targetRoutingError &&
    !launching &&
    !resourceErrors.cpu &&
    !resourceErrors.memory &&
    !resourceErrors.gpu;

  async function handleLaunch() {
    if (!canLaunch) {
      setLaunchError(
        sessionNameError ?? targetRoutingError ?? 'Fill in the required launch fields first.',
      );
      return;
    }

    setLaunchError(null);
    setCreatedSessionId(null);
    setStep('booting');
    setBootStep(0);
    setBootProgress(0);
    setLaunching(true);

    try {
      let launchSpec: string | undefined;
      let launchSpecId: string | undefined;
      const selectedPreset = presets.find((preset) => launchSpecRef(preset) === form.presetId);
      const currentPresetPayload = buildPresetRuntimePayload(
        form,
        selectedPreset?.name ?? `${effectiveSessionName}-runtime`,
      );

      if (
        hasPresetBackedRuntime(form) &&
        (!selectedPreset ||
          JSON.stringify(buildPresetComparisonPayload(selectedPreset)) !==
            JSON.stringify(currentPresetPayload))
      ) {
        const savedPreset = await volundr.saveLaunchSpec(currentPresetPayload);
        launchSpecId = savedPreset.id ?? undefined;
        launchSpec = undefined;
        setPresets((current) => {
          const next = current.filter((preset) => preset.id !== savedPreset.id);
          return [...next, savedPreset];
        });
        setForm((current) => ({ ...current, presetId: launchSpecRef(savedPreset) }));
      } else if (selectedPreset?.scope === 'system') {
        launchSpec = selectedPreset.name;
      } else if (selectedPreset?.id) {
        launchSpecId = selectedPreset.id;
      }

      const session = await volundr.startSession({
        name: effectiveSessionName,
        personaName: form.personaName || undefined,
        source: buildSessionSource(form),
        model: form.model.trim(),
        launchSpec,
        launchSpecId,
        definition: form.definition,
        taskType: definitionToTaskType(form.definition),
        trackerIssue: form.trackerIssue ?? undefined,
        terminalRestricted: false,
        instanceId: form.targetMode === 'instance' ? form.instanceId || undefined : undefined,
        targetTags: form.targetMode === 'tags' ? form.targetTags : undefined,
        targetMatch: form.targetMode === 'tags' ? form.targetMatch : undefined,
        workspaceId: form.workspaceId || undefined,
        credentialNames: form.selectedCredentials.length ? form.selectedCredentials : undefined,
        integrationIds: form.selectedIntegrations.length ? form.selectedIntegrations : undefined,
        resourceConfig: buildResourceConfig(form),
        systemPrompt: form.systemPrompt.trim() || undefined,
        initialPrompt: form.initialPrompt.trim() || undefined,
        workloadConfig: buildWorkloadConfig(form),
      });

      setCreatedSessionId(session.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      ]);
    } catch (error) {
      setLaunchError(error instanceof Error ? error.message : 'Failed to launch session');
      setStep('confirm');
    } finally {
      setLaunching(false);
    }
  }

  function handleNext() {
    if (isLastStep) {
      void handleLaunch();
      return;
    }
    if (stepIdx < STEPS.length - 1) {
      setStep(STEPS[stepIdx + 1]!);
    }
  }

  function handleBack() {
    if (stepIdx > 0) {
      setStep(STEPS[stepIdx - 1]!);
    }
  }

  // Reset on open
  useEffect(() => {
    if (open) {
      queueMicrotask(() => {
        setStep('source');
        setBootStep(0);
        setBootProgress(0);
        setLaunchError(null);
        setCreatedSessionId(null);
        setLaunching(false);
      });
    }
  }, [open]);

  return {
    availableMcpServers,
    bootProgress,
    bootStep,
    canGoBack,
    canLaunch,
    clusterResources,
    createdSessionId,
    credentials,
    form,
    handleApplyPreset,
    handleBack,
    handleNext,
    handleSavePreset,
    integrations,
    isLastStep,
    launchError,
    launching,
    manualBranches,
    models,
    navigate,
    personas,
    presets,
    repos,
    sessionDefinitions,
    step,
    targets,
    trackerLoading,
    trackerResults,
    update,
    workspaces,
  };
}
