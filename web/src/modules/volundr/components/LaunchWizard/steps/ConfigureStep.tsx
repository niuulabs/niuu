import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  ChevronDown,
  ChevronRight,
  FolderGit2,
  Cpu,
  Plus,
  X,
  Server,
  Terminal,
  Shield,
  FileText,
  Settings2,
  Layers,
  Code2,
  Save,
  HardDrive,
  KeyRound,
  Plug,
  HardDriveDownload,
} from 'lucide-react';
import { cn } from '@/utils';
import { serializePresetYaml, parsePresetYaml } from '@/utils/presetYaml';
import { parseK8sQuantity, formatHumanBytes, formatResourceValue } from '@/utils/k8sQuantity';
import { validateSessionName } from '@/utils/sessionName';
import type {
  VolundrPreset,
  VolundrRepo,
  VolundrModel,
  McpServerConfig,
  McpServerType,
  TrackerIssue,
  RepoProvider,
  VolundrWorkspace,
  StoredCredential,
  IntegrationConnection,
  ClusterResourceInfo,
  SessionDefinition,
} from '@/modules/volundr/models';
import type { SourceType } from '../LaunchWizard';
import type { IVolundrService } from '@/modules/volundr/ports';
import { TrackerIssueSearch } from '@/modules/volundr/components/molecules/TrackerIssueSearch';
import type { WizardState } from '../LaunchWizard';
import styles from './ConfigureStep.module.css';

export interface ConfigureStepProps {
  state: WizardState;
  presets: VolundrPreset[];
  repos: VolundrRepo[];
  models: Record<string, VolundrModel>;
  availableMcpServers: McpServerConfig[];
  availableSecrets: string[];
  service: IVolundrService;
  searchTrackerIssues?: (query: string) => Promise<TrackerIssue[]>;
  localMountsEnabled?: boolean;
  miniMode?: boolean;
  onChange: (updates: Partial<WizardState>) => void;
  onSavePreset: (
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ) => Promise<VolundrPreset>;
}

/**
 * Validate a resource input value against the available capacity.
 * Returns an error string or null if valid.
 */
function validateResourceInput(
  inputValue: string,
  unit: string,
  totalAvailable: number
): string | null {
  if (!inputValue.trim()) return null;

  const parsed = parseK8sQuantity(inputValue, unit);
  if (isNaN(parsed)) {
    if (unit === 'bytes') return 'Invalid format. Use e.g. 4Gi, 512Mi, 1Ti';
    if (unit === 'cores') return 'Invalid format. Use e.g. 4, 500m, 1.5';
    return 'Invalid number';
  }

  if (parsed <= 0) return 'Must be greater than 0';

  if (totalAvailable > 0 && parsed > totalAvailable) {
    const availFormatted =
      unit === 'bytes'
        ? formatHumanBytes(totalAvailable)
        : `${Number.isInteger(totalAvailable) ? totalAvailable : totalAvailable.toFixed(1)} ${unit}`;
    return `Exceeds available capacity (${availFormatted})`;
  }

  return null;
}

function workspaceLabel(ws: VolundrWorkspace): string {
  if (ws.sessionName) return ws.sessionName;
  if (ws.sourceUrl) {
    const repoName = ws.sourceUrl.replace(/.*\//, '').replace(/\.git$/, '');
    const ref = ws.sourceRef || 'main';
    return `${repoName} / ${ref}`;
  }
  return ws.pvcName;
}

const FALLBACK_DEFINITIONS: SessionDefinition[] = [
  {
    key: 'skuldClaude',
    displayName: 'Claude Code',
    description: 'Anthropic Claude CLI agent',
    labels: ['claude'],
    defaultModel: 'claude-sonnet',
  },
  {
    key: 'skuldCodex',
    displayName: 'Codex',
    description: 'OpenAI Codex CLI agent',
    labels: ['codex'],
    defaultModel: 'codex',
  },
];

const PROVIDER_LABELS: Record<RepoProvider, string> = {
  github: 'GitHub',
  gitlab: 'GitLab',
  bitbucket: 'Bitbucket',
};

export function ConfigureStep({
  state,
  presets,
  repos,
  models,
  availableMcpServers,
  availableSecrets,
  service,
  searchTrackerIssues,
  localMountsEnabled = false,
  miniMode = false,
  onChange,
  onSavePreset,
}: ConfigureStepProps) {
  const [workspaces, setWorkspaces] = useState<VolundrWorkspace[]>([]);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [isSavingPreset, setIsSavingPreset] = useState(false);
  const [savePresetName, setSavePresetName] = useState('');
  const [showSavePreset, setShowSavePreset] = useState(false);
  const [showMcpPicker, setShowMcpPicker] = useState(false);
  const [showMcpForm, setShowMcpForm] = useState(false);
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [presetWarnings, setPresetWarnings] = useState<string[]>([]);
  const [customMcpName, setCustomMcpName] = useState('');
  const [customMcpType, setCustomMcpType] = useState<McpServerType>('stdio');
  const [customMcpCommand, setCustomMcpCommand] = useState('');
  const [customMcpArgs, setCustomMcpArgs] = useState('');
  const [customMcpUrl, setCustomMcpUrl] = useState('');
  const [customMcpEnvKey, setCustomMcpEnvKey] = useState('');
  const [customMcpEnvVal, setCustomMcpEnvVal] = useState('');
  const [customMcpEnv, setCustomMcpEnv] = useState<Record<string, string>>({});
  const [newEnvKey, setNewEnvKey] = useState('');
  const [newEnvVal, setNewEnvVal] = useState('');
  const [showAllWorkspaces, setShowAllWorkspaces] = useState(false);

  const [credentials, setCredentials] = useState<StoredCredential[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationConnection[]>([]);
  const [clusterResources, setClusterResources] = useState<ClusterResourceInfo | null>(null);
  const [sessionDefinitions, setSessionDefinitions] =
    useState<SessionDefinition[]>(FALLBACK_DEFINITIONS);

  useEffect(() => {
    Promise.all([service.listWorkspaces('archived'), service.listWorkspaces('active')])
      .then(([archived, active]) => setWorkspaces([...archived, ...active]))
      .catch(() => {});
    service
      .getCredentials()
      .then(setCredentials)
      .catch(() => {});
    service
      .getIntegrations()
      .then(setIntegrations)
      .catch(() => {});
    service
      .getClusterResources()
      .then(setClusterResources)
      .catch(() => {});
    service
      .getSessionDefinitions()
      .then(defs => {
        if (defs.length > 0) {
          setSessionDefinitions(defs);
        }
      })
      .catch(() => {});
  }, [service]);

  // Derive GPU types from cluster resources for dropdown
  const gpuTypes = useMemo(() => {
    if (!clusterResources) return [];
    return clusterResources.resourceTypes
      .filter(rt => rt.category === 'accelerator' && rt.name.startsWith('gpu_'))
      .map(rt => ({
        label: rt.displayName,
        value: rt.name.replace('gpu_', ''),
      }));
  }, [clusterResources]);

  // Aggregate available resources across nodes per resource type
  const aggregatedResources = useMemo(() => {
    if (!clusterResources || clusterResources.nodes.length === 0) return [];
    return clusterResources.resourceTypes
      .filter(rt => !rt.name.startsWith('gpu_'))
      .map(rt => {
        let totalAvailable = 0;
        for (const node of clusterResources.nodes) {
          const val = node.available[rt.resourceKey];
          if (val) {
            const parsed = parseK8sQuantity(val, rt.unit);
            if (!isNaN(parsed)) totalAvailable += parsed;
          }
        }
        return { resourceType: rt, totalAvailable };
      });
  }, [clusterResources]);

  // Check if user has requested any GPU resources
  const hasGpuRequested = useMemo(() => {
    const gpuVal = state.resourceConfig.gpu;
    if (gpuVal && gpuVal !== '0') return true;
    // Also check dynamically discovered GPU resource types
    return aggregatedResources.some(
      ar =>
        ar.resourceType.category === 'accelerator' &&
        state.resourceConfig[ar.resourceType.name] &&
        state.resourceConfig[ar.resourceType.name] !== '0'
    );
  }, [state.resourceConfig, aggregatedResources]);

  // Merge availableSecrets and stored credentials into a unified list
  const allCredentials = useMemo(() => {
    const credentialNames = new Set(credentials.map(c => c.name));
    const secretOnlyItems = availableSecrets
      .filter(s => !credentialNames.has(s))
      .map(s => ({ name: s, source: 'secret' as const }));
    const credItems = credentials.map(c => ({ name: c.name, source: 'credential' as const }));
    return [...credItems, ...secretOnlyItems];
  }, [credentials, availableSecrets]);

  const selectedWorkspace = workspaces.find(ws => ws.id === state.workspaceId);

  const filteredWorkspaces = useMemo(() => {
    if (showAllWorkspaces || !state.repo) return workspaces;
    return workspaces.filter(ws => {
      if (!ws.sourceUrl) return false;
      const normalize = (url: string) =>
        url
          .replace(/^https?:\/\//, '')
          .replace(/\.git$/, '')
          .replace(/\/$/, '');
      return normalize(ws.sourceUrl) === normalize(state.repo);
    });
  }, [workspaces, state.repo, showAllWorkspaces]);

  const currentRepo = repos.find(r => r.cloneUrl === state.repo);
  const branches = currentRepo?.branches ?? [];

  const reposByProvider = useMemo(
    () =>
      repos.reduce<Record<RepoProvider, VolundrRepo[]>>(
        (acc, repo) => {
          acc[repo.provider] = acc[repo.provider] || [];
          acc[repo.provider].push(repo);
          return acc;
        },
        {} as Record<RepoProvider, VolundrRepo[]>
      ),
    [repos]
  );

  // Filter models based on CLI tool
  const filteredModels = useMemo(() => {
    return Object.entries(models);
  }, [models]);

  const handleRepoChange = useCallback(
    (cloneUrl: string) => {
      const repo = repos.find(r => r.cloneUrl === cloneUrl);
      onChange({ repo: cloneUrl, branch: repo?.defaultBranch ?? '' });
    },
    [repos, onChange]
  );

  const handlePresetSelect = useCallback(
    (presetId: string) => {
      if (!presetId) {
        onChange({ preset: null });
        setPresetWarnings([]);
        return;
      }
      const preset = presets.find(p => p.id === presetId);
      if (!preset) {
        return;
      }

      const warnings: string[] = [];
      // Find matching definition for the preset's CLI tool
      const matchedDef = sessionDefinitions.find(d => d.labels.includes(preset.cliTool));
      const updates: Partial<WizardState> = {
        preset,
        model: preset.model ?? '',
        taskType: `skuld-${preset.cliTool}`,
        definition: matchedDef?.key ?? (preset.cliTool === 'codex' ? 'skuldCodex' : 'skuldClaude'),
        systemPrompt: preset.systemPrompt ?? '',
        mcpServers: [...preset.mcpServers],
        resourceConfig: { ...preset.resourceConfig },
        envVars: { ...preset.envVars },
        setupScripts: [...preset.setupScripts],
        template: {
          ...state.template,
          cliTool: preset.cliTool,
          workloadType: preset.workloadType,
          terminalSidecar: { ...preset.terminalSidecar },
          skills: [...preset.skills],
          rules: [...preset.rules],
          workloadConfig: { ...preset.workloadConfig },
        },
      };

      // Validate source (repo)
      if (preset.source) {
        const src = preset.source;
        if (src.type === 'git') {
          const matchedRepo = repos.find(r => r.cloneUrl === src.repo);
          if (matchedRepo) {
            updates.sourceType = 'git';
            updates.repo = src.repo;
            updates.branch = src.branch ?? matchedRepo.defaultBranch;
          } else {
            warnings.push(`Repository "${src.repo}" is no longer available`);
          }
        } else if (src.type === 'local_mount') {
          updates.sourceType = 'local_mount';
          if (src.local_path) {
            updates.mountPaths = [{ host_path: src.local_path, mount_path: '', read_only: false }];
          } else if (src.paths) {
            updates.mountPaths = [...src.paths];
          }
        }
      }

      // Validate credentials
      const validCreds = preset.envSecretRefs.filter(s => availableSecrets.includes(s));
      const missingCreds = preset.envSecretRefs.filter(s => !availableSecrets.includes(s));
      for (const c of missingCreds) {
        warnings.push(`Credential "${c}" is no longer available`);
      }
      updates.selectedCredentials = validCreds;

      // Validate integrations
      const enabledIntegrationIds = new Set(integrations.filter(i => i.enabled).map(i => i.id));
      const validIntegrations = preset.integrationIds.filter(id => enabledIntegrationIds.has(id));
      const missingIntegrations = preset.integrationIds.filter(
        id => !enabledIntegrationIds.has(id)
      );
      for (const id of missingIntegrations) {
        const slug = integrations.find(i => i.id === id)?.slug ?? id;
        warnings.push(`Integration "${slug}" is no longer available`);
      }
      updates.selectedIntegrations = validIntegrations;

      setPresetWarnings(warnings);
      onChange(updates);
    },
    [presets, repos, availableSecrets, integrations, sessionDefinitions, state.template, onChange]
  );

  const handleToggleYaml = useCallback(() => {
    if (!state.yamlMode) {
      // Switching to YAML mode: serialize current state
      const source: import('@/models').SessionSource | null =
        state.sourceType === 'git' && state.repo
          ? { type: 'git', repo: state.repo, branch: state.branch }
          : state.sourceType === 'local_mount' &&
              state.mountPaths.some(p => p.host_path && p.mount_path)
            ? {
                type: 'local_mount',
                paths: state.mountPaths.filter(p => p.host_path && p.mount_path),
              }
            : null;
      const yamlContent = serializePresetYaml({
        cliTool: state.template.cliTool,
        workloadType: state.template.workloadType,
        model: state.model,
        systemPrompt: state.systemPrompt,
        resourceConfig: state.resourceConfig,
        mcpServers: state.mcpServers,
        terminalSidecar: state.template.terminalSidecar,
        skills: state.template.skills,
        rules: state.template.rules,
        envVars: state.envVars,
        envSecretRefs: state.selectedCredentials,
        source,
        integrationIds: state.selectedIntegrations,
        setupScripts: state.setupScripts,
        workloadConfig: state.template.workloadConfig,
      });
      onChange({ yamlMode: true, yamlContent });
      setYamlError(null);
      return;
    }

    // Switching back to form mode: parse YAML
    try {
      const parsed = parsePresetYaml(state.yamlContent);
      const updates: Partial<WizardState> = { yamlMode: false };
      if (parsed.model !== undefined) updates.model = parsed.model;
      if (parsed.systemPrompt !== undefined) updates.systemPrompt = parsed.systemPrompt;
      if (parsed.mcpServers) updates.mcpServers = parsed.mcpServers;
      if (parsed.resourceConfig) updates.resourceConfig = parsed.resourceConfig;
      if (parsed.envVars) updates.envVars = parsed.envVars;
      if (parsed.envSecretRefs) updates.selectedCredentials = parsed.envSecretRefs;
      if (parsed.source !== undefined) {
        if (parsed.source && parsed.source.type === 'git') {
          updates.sourceType = 'git';
          updates.repo = parsed.source.repo;
          updates.branch = parsed.source.branch;
        } else if (parsed.source && parsed.source.type === 'local_mount') {
          updates.sourceType = 'local_mount';
          if (parsed.source.local_path) {
            updates.mountPaths = [
              { host_path: parsed.source.local_path, mount_path: '', read_only: false },
            ];
          } else if (parsed.source.paths) {
            updates.mountPaths = [...parsed.source.paths];
          }
        }
      }
      if (parsed.integrationIds) updates.selectedIntegrations = parsed.integrationIds;
      if (parsed.setupScripts) updates.setupScripts = parsed.setupScripts;

      // Template-nested fields
      const templateUpdates = { ...state.template };
      if (parsed.cliTool) templateUpdates.cliTool = parsed.cliTool;
      if (parsed.workloadType) templateUpdates.workloadType = parsed.workloadType;
      if (parsed.terminalSidecar) templateUpdates.terminalSidecar = parsed.terminalSidecar;
      if (parsed.skills) templateUpdates.skills = parsed.skills;
      if (parsed.rules) templateUpdates.rules = parsed.rules;
      if (parsed.workloadConfig) templateUpdates.workloadConfig = parsed.workloadConfig;
      updates.template = templateUpdates;

      onChange(updates);
      setYamlError(null);
    } catch (err) {
      setYamlError(err instanceof Error ? err.message : 'Invalid YAML');
    }
  }, [state, onChange]);

  const handleTrackerSelect = useCallback(
    (issue: TrackerIssue) => {
      const updates: Partial<WizardState> = { trackerIssue: issue };
      if (!state.name) {
        updates.name = issue.identifier.toLowerCase();
      }
      onChange(updates);
    },
    [state.name, onChange]
  );

  const handleTrackerClear = useCallback(() => {
    onChange({ trackerIssue: undefined });
  }, [onChange]);

  // MCP server management
  const handleAddMcpServer = useCallback(
    (server: McpServerConfig) => {
      if (state.mcpServers.some(s => s.name === server.name)) {
        return;
      }
      onChange({ mcpServers: [...state.mcpServers, server] });
    },
    [state.mcpServers, onChange]
  );

  const handleRemoveMcpServer = useCallback(
    (name: string) => {
      onChange({ mcpServers: state.mcpServers.filter(s => s.name !== name) });
    },
    [state.mcpServers, onChange]
  );

  const resetMcpForm = useCallback(() => {
    setCustomMcpName('');
    setCustomMcpType('stdio');
    setCustomMcpCommand('');
    setCustomMcpArgs('');
    setCustomMcpUrl('');
    setCustomMcpEnvKey('');
    setCustomMcpEnvVal('');
    setCustomMcpEnv({});
    setShowMcpForm(false);
    setShowMcpPicker(false);
  }, []);

  const handleAddCustomMcp = useCallback(() => {
    if (!customMcpName.trim()) {
      return;
    }
    const server: McpServerConfig = {
      name: customMcpName.trim(),
      type: customMcpType,
    };
    if (customMcpType === 'stdio') {
      server.command = customMcpCommand.trim() || undefined;
      const args = customMcpArgs.trim();
      if (args) {
        server.args = args.split(/\s+/);
      }
    } else {
      server.url = customMcpUrl.trim() || undefined;
    }
    if (Object.keys(customMcpEnv).length > 0) {
      server.env = { ...customMcpEnv };
    }
    handleAddMcpServer(server);
    resetMcpForm();
  }, [
    customMcpName,
    customMcpType,
    customMcpCommand,
    customMcpArgs,
    customMcpUrl,
    customMcpEnv,
    handleAddMcpServer,
    resetMcpForm,
  ]);

  // Restrict terminal shell — implicitly enables the terminal sidecar
  const handleToggleTerminalRestricted = useCallback(() => {
    const nowRestricted = !state.terminalRestricted;
    onChange({
      terminalRestricted: nowRestricted,
      template: {
        ...state.template,
        terminalSidecar: {
          ...state.template.terminalSidecar,
          enabled: nowRestricted,
        },
      },
    });
  }, [state.template, state.terminalRestricted, onChange]);

  const handleAddAllowedCommand = useCallback(() => {
    onChange({
      template: {
        ...state.template,
        terminalSidecar: {
          ...state.template.terminalSidecar,
          allowedCommands: [...state.template.terminalSidecar.allowedCommands, ''],
        },
      },
    });
  }, [state.template, onChange]);

  const handleUpdateAllowedCommand = useCallback(
    (idx: number, value: string) => {
      const cmds = [...state.template.terminalSidecar.allowedCommands];
      cmds[idx] = value;
      onChange({
        template: {
          ...state.template,
          terminalSidecar: { ...state.template.terminalSidecar, allowedCommands: cmds },
        },
      });
    },
    [state.template, onChange]
  );

  const handleRemoveAllowedCommand = useCallback(
    (idx: number) => {
      const cmds = state.template.terminalSidecar.allowedCommands.filter((_, i) => i !== idx);
      onChange({
        template: {
          ...state.template,
          terminalSidecar: { ...state.template.terminalSidecar, allowedCommands: cmds },
        },
      });
    },
    [state.template, onChange]
  );

  // Environment variables
  const handleAddEnvVar = useCallback(() => {
    if (!newEnvKey.trim()) {
      return;
    }
    onChange({ envVars: { ...state.envVars, [newEnvKey.trim()]: newEnvVal } });
    setNewEnvKey('');
    setNewEnvVal('');
  }, [newEnvKey, newEnvVal, state.envVars, onChange]);

  const handleRemoveEnvVar = useCallback(
    (key: string) => {
      const next = { ...state.envVars };
      delete next[key];
      onChange({ envVars: next });
    },
    [state.envVars, onChange]
  );

  // Credential selection
  const handleToggleCredential = useCallback(
    (name: string) => {
      if (state.selectedCredentials.includes(name)) {
        onChange({ selectedCredentials: state.selectedCredentials.filter(c => c !== name) });
      } else {
        onChange({ selectedCredentials: [...state.selectedCredentials, name] });
      }
    },
    [state.selectedCredentials, onChange]
  );

  // Integration selection
  const handleToggleIntegration = useCallback(
    (id: string) => {
      if (state.selectedIntegrations.includes(id)) {
        onChange({ selectedIntegrations: state.selectedIntegrations.filter(i => i !== id) });
      } else {
        onChange({ selectedIntegrations: [...state.selectedIntegrations, id] });
      }
    },
    [state.selectedIntegrations, onChange]
  );

  // Save as preset
  const handleSavePreset = useCallback(async () => {
    if (!savePresetName.trim()) {
      return;
    }
    setIsSavingPreset(true);
    try {
      const source: import('@/models').SessionSource | null =
        state.sourceType === 'git' && state.repo
          ? { type: 'git', repo: state.repo, branch: state.branch }
          : state.sourceType === 'local_mount' &&
              state.mountPaths.some(p => p.host_path && p.mount_path)
            ? {
                type: 'local_mount',
                paths: state.mountPaths.filter(p => p.host_path && p.mount_path),
              }
            : null;

      const saved = await onSavePreset({
        name: savePresetName.trim(),
        description: '',
        isDefault: false,
        cliTool: state.template.cliTool,
        workloadType: state.template.workloadType,
        model: state.model || null,
        systemPrompt: state.systemPrompt || null,
        resourceConfig: state.resourceConfig,
        mcpServers: state.mcpServers,
        terminalSidecar: state.template.terminalSidecar,
        skills: state.template.skills,
        rules: state.template.rules,
        envVars: state.envVars,
        envSecretRefs: state.selectedCredentials,
        source,
        integrationIds: state.selectedIntegrations,
        setupScripts: state.setupScripts,
        workloadConfig: state.template.workloadConfig,
      });
      onChange({ preset: saved });
      setSavePresetName('');
      setShowSavePreset(false);
    } finally {
      setIsSavingPreset(false);
    }
  }, [savePresetName, state, onSavePreset, onChange]);

  // Setup scripts
  const handleAddScript = useCallback(() => {
    onChange({ setupScripts: [...state.setupScripts, ''] });
  }, [state.setupScripts, onChange]);

  const handleUpdateScript = useCallback(
    (idx: number, value: string) => {
      const scripts = [...state.setupScripts];
      scripts[idx] = value;
      onChange({ setupScripts: scripts });
    },
    [state.setupScripts, onChange]
  );

  const handleRemoveScript = useCallback(
    (idx: number) => {
      onChange({ setupScripts: state.setupScripts.filter((_, i) => i !== idx) });
    },
    [state.setupScripts, onChange]
  );

  return (
    <div className={styles.container}>
      {/* Preset selector */}
      {presets.length > 0 && (
        <div className={styles.presetSection}>
          <label className={styles.formLabel}>
            <Layers className={styles.formLabelIcon} />
            Load Preset
          </label>
          <select
            className={styles.formSelect}
            value={state.preset?.id ?? ''}
            onChange={e => handlePresetSelect(e.target.value)}
          >
            <option value="">Custom (no preset)</option>
            {presets.map(preset => (
              <option key={preset.id} value={preset.id}>
                {preset.name}
                {preset.isDefault ? ' (default)' : ''}
              </option>
            ))}
          </select>
          {state.preset && (
            <span className={styles.presetDescription}>{state.preset.description}</span>
          )}
          {presetWarnings.length > 0 && (
            <div className={styles.presetWarnings}>
              <div className={styles.presetWarningsHeader}>
                <span>Preset loaded with warnings</span>
                <button
                  type="button"
                  className={styles.presetWarningsDismiss}
                  onClick={() => setPresetWarnings([])}
                >
                  <X size={14} />
                </button>
              </div>
              <ul className={styles.presetWarningsList}>
                {presetWarnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Session Definition selector */}
      <div className={styles.cliToolSection}>
        <span className={styles.sectionLabel}>CLI Tool</span>
        <div className={styles.cliToolGrid}>
          {sessionDefinitions.map(def => (
            <button
              key={def.key}
              className={cn(
                styles.cliToolCard,
                state.definition === def.key && styles.cliToolCardActive
              )}
              onClick={() =>
                onChange({
                  definition: def.key,
                  taskType: `skuld-${def.labels[0] ?? def.key}`,
                  template: { ...state.template, cliTool: def.labels[0] ?? def.key },
                })
              }
              type="button"
            >
              <span className={styles.cliToolLabel}>{def.displayName}</span>
              <span className={styles.cliToolDescription}>{def.description}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Basic Fields */}
      <div className={styles.formSection}>
        {/* Session Name */}
        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            <Terminal className={styles.formLabelIcon} />
            Session Name
            <span className={styles.required}>*</span>
            <span className={styles.formLabelHint}>(lowercase letters, digits, hyphens)</span>
          </label>
          <input
            className={cn(
              styles.formInput,
              validateSessionName(state.name) && styles.formInputError
            )}
            type="text"
            placeholder="e.g. feature-auth-refactor"
            value={state.name}
            onChange={e => onChange({ name: e.target.value })}
            required
            maxLength={63}
          />
          {validateSessionName(state.name) && (
            <span className={styles.resourceError}>{validateSessionName(state.name)}</span>
          )}
        </div>

        {/* Issue */}
        {searchTrackerIssues && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>Issue</label>
            <TrackerIssueSearch
              onSelect={handleTrackerSelect}
              onClear={handleTrackerClear}
              selectedIssue={state.trackerIssue ?? null}
              onSearch={searchTrackerIssues}
            />
          </div>
        )}

        {/* Source Type Toggle — only show when local mounts are enabled */}
        {localMountsEnabled && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              Workspace Source
              <span className={styles.required}>*</span>
            </label>
            <div className={styles.sourceTypeToggle}>
              <button
                type="button"
                className={cn(
                  styles.sourceTypeButton,
                  state.sourceType === 'git' && styles.sourceTypeActive
                )}
                onClick={() => onChange({ sourceType: 'git' as SourceType })}
              >
                <FolderGit2 className={styles.formLabelIcon} />
                Git Repository
              </button>
              <button
                type="button"
                className={cn(
                  styles.sourceTypeButton,
                  state.sourceType === 'local_mount' && styles.sourceTypeActive
                )}
                onClick={() => onChange({ sourceType: 'local_mount' as SourceType })}
              >
                <HardDriveDownload className={styles.formLabelIcon} />
                Local Mount
              </button>
            </div>
          </div>
        )}

        {state.sourceType === 'git' && (
          <>
            {/* Repository */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                <FolderGit2 className={styles.formLabelIcon} />
                Repository
                <span className={styles.required}>*</span>
              </label>
              <select
                className={styles.formSelect}
                value={state.repo}
                onChange={e => handleRepoChange(e.target.value)}
              >
                <option value="">Select repository...</option>
                {Object.entries(reposByProvider).map(([provider, providerRepos]) => (
                  <optgroup key={provider} label={PROVIDER_LABELS[provider as RepoProvider]}>
                    {providerRepos.map(repo => (
                      <option key={repo.cloneUrl} value={repo.cloneUrl}>
                        {repo.org}/{repo.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>

            {/* Branch */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                Branch
                <span className={styles.required}>*</span>
              </label>
              <select
                className={styles.formSelect}
                value={state.branch}
                onChange={e => onChange({ branch: e.target.value })}
                disabled={!state.repo}
              >
                <option value="">Select branch...</option>
                {branches.map(branch => (
                  <option key={branch} value={branch}>
                    {branch}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}

        {state.sourceType === 'local_mount' && miniMode && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              <HardDriveDownload className={styles.formLabelIcon} />
              Project Directory
              <span className={styles.required}>*</span>
            </label>
            <input
              className={styles.formInput}
              type="text"
              placeholder="Absolute path to your project (e.g. /Users/you/projects/my-app)"
              value={state.mountPaths[0]?.host_path ?? ''}
              onChange={e => {
                onChange({
                  mountPaths: [{ host_path: e.target.value, mount_path: '', read_only: false }],
                });
              }}
            />
          </div>
        )}

        {state.sourceType === 'local_mount' && !miniMode && (
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              <HardDriveDownload className={styles.formLabelIcon} />
              Mount Paths
              <span className={styles.required}>*</span>
            </label>
            <div className={styles.mountEditor}>
              {state.mountPaths.map((mapping, idx) => (
                <div key={idx} className={styles.mountRow}>
                  <input
                    className={styles.formInput}
                    type="text"
                    placeholder="Host path (e.g. /home/user/project)"
                    value={mapping.host_path}
                    onChange={e => {
                      const updated = [...state.mountPaths];
                      updated[idx] = { ...mapping, host_path: e.target.value };
                      onChange({ mountPaths: updated });
                    }}
                  />
                  <input
                    className={styles.formInput}
                    type="text"
                    placeholder="Container path (e.g. /workspace)"
                    value={mapping.mount_path}
                    onChange={e => {
                      const updated = [...state.mountPaths];
                      updated[idx] = { ...mapping, mount_path: e.target.value };
                      onChange({ mountPaths: updated });
                    }}
                  />
                  <label className={styles.mountReadOnly}>
                    <input
                      type="checkbox"
                      checked={mapping.read_only}
                      onChange={e => {
                        const updated = [...state.mountPaths];
                        updated[idx] = { ...mapping, read_only: e.target.checked };
                        onChange({ mountPaths: updated });
                      }}
                    />
                    Read-only
                  </label>
                  {state.mountPaths.length > 1 && (
                    <button
                      type="button"
                      className={styles.mountRemoveButton}
                      onClick={() => {
                        const updated = state.mountPaths.filter((_, i) => i !== idx);
                        onChange({ mountPaths: updated });
                      }}
                    >
                      <X className={styles.buttonIcon} />
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                className={styles.addMountButton}
                onClick={() =>
                  onChange({
                    mountPaths: [
                      ...state.mountPaths,
                      { host_path: '', mount_path: '', read_only: true },
                    ],
                  })
                }
              >
                <Plus className={styles.addIcon} />
                Add mount
              </button>
            </div>
          </div>
        )}

        {/* Model */}
        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            <Cpu className={styles.formLabelIcon} />
            Model
            <span className={styles.required}>*</span>
          </label>
          <select
            className={styles.formSelect}
            value={state.model}
            onChange={e => onChange({ model: e.target.value })}
          >
            <option value="">Select model...</option>
            {filteredModels.map(([key, model]) => (
              <option key={key} value={key}>
                {model.provider === 'local' ? '⚡ ' : '☁ '}
                {model.name} ({model.tier})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Workspace */}
      {workspaces.length > 0 && (
        <div className={styles.formSection}>
          <div className={styles.formGroup}>
            <label className={styles.formLabel}>
              <HardDrive className={styles.formLabelIcon} />
              Workspace
            </label>
            <select
              className={styles.formSelect}
              value={state.workspaceId || ''}
              onChange={e => onChange({ workspaceId: e.target.value || undefined })}
            >
              <option value="">New workspace</option>
              {filteredWorkspaces.map(ws => (
                <option key={ws.id} value={ws.id}>
                  {workspaceLabel(ws)} ({ws.sizeGb}Gi) — {ws.status}{' '}
                  {new Date(ws.archivedAt || ws.createdAt).toLocaleDateString()}
                </option>
              ))}
            </select>
            {state.repo && workspaces.length > 0 && (
              <label className={styles.workspaceFilterToggle}>
                <input
                  type="checkbox"
                  checked={showAllWorkspaces}
                  onChange={e => {
                    setShowAllWorkspaces(e.target.checked);
                    if (!e.target.checked) onChange({ workspaceId: undefined });
                  }}
                />
                <span>Show all existing workspaces</span>
              </label>
            )}
            {selectedWorkspace && (
              <div className={styles.workspaceInfo}>
                {workspaceLabel(selectedWorkspace)} · {selectedWorkspace.sizeGb}Gi ·{' '}
                {selectedWorkspace.status}{' '}
                {new Date(
                  selectedWorkspace.archivedAt || selectedWorkspace.createdAt
                ).toLocaleDateString()}
              </div>
            )}
            {filteredWorkspaces.length === 0 && workspaces.length > 0 && !showAllWorkspaces && (
              <div className={styles.workspaceInfo}>
                No existing workspaces match the selected repository
              </div>
            )}
          </div>
        </div>
      )}

      {/* Advanced Section */}
      <div className={styles.advancedSection}>
        <div className={styles.advancedHeader}>
          <button
            className={styles.advancedToggle}
            onClick={() => setShowAdvanced(prev => !prev)}
            type="button"
            aria-expanded={showAdvanced}
          >
            {showAdvanced ? (
              <ChevronDown className={styles.advancedChevron} />
            ) : (
              <ChevronRight className={styles.advancedChevron} />
            )}
            <Settings2 className={styles.advancedIcon} />
            Advanced Configuration
          </button>

          {showAdvanced && (
            <button className={styles.yamlToggle} onClick={handleToggleYaml} type="button">
              <Code2 className={styles.yamlToggleIcon} />
              {state.yamlMode ? 'Form View' : 'Edit as YAML'}
            </button>
          )}
        </div>

        {showAdvanced && state.yamlMode && (
          <div className={styles.yamlSection}>
            <textarea
              className={styles.yamlEditor}
              value={state.yamlContent}
              onChange={e => onChange({ yamlContent: e.target.value })}
              spellCheck={false}
              rows={20}
            />
            {yamlError && <span className={styles.yamlError}>{yamlError}</span>}
          </div>
        )}

        {showAdvanced && !state.yamlMode && (
          <div className={styles.advancedContent}>
            {/* System Prompt */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                <FileText className={styles.formLabelIcon} />
                System Prompt
              </label>
              <textarea
                className={styles.formTextarea}
                value={state.systemPrompt}
                onChange={e => onChange({ systemPrompt: e.target.value })}
                placeholder="Optional system prompt..."
                rows={4}
              />
            </div>

            {/* Initial Prompt */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                <FileText className={styles.formLabelIcon} />
                Initial Prompt
              </label>
              <textarea
                className={styles.formTextarea}
                value={state.initialPrompt}
                onChange={e => onChange({ initialPrompt: e.target.value })}
                placeholder="What should the session work on? e.g. 'Fix the login bug in auth.py'"
                rows={4}
              />
            </div>

            {/* MCP Servers */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                <Server className={styles.formLabelIcon} />
                MCP Servers
              </label>

              {state.mcpServers.length > 0 && (
                <div className={styles.itemList}>
                  {state.mcpServers.map(server => (
                    <div key={server.name} className={styles.mcpCard}>
                      <div className={styles.mcpCardHeader}>
                        <span className={styles.listItemText}>
                          <Server className={styles.listItemIcon} />
                          {server.name}
                          <span className={styles.mcpTypeBadge}>{server.type}</span>
                        </span>
                        <button
                          className={styles.removeButton}
                          onClick={() => handleRemoveMcpServer(server.name)}
                          type="button"
                          aria-label={`Remove ${server.name}`}
                        >
                          <X className={styles.removeIcon} />
                        </button>
                      </div>
                      {server.type === 'stdio' && server.command && (
                        <span className={styles.listItemMeta}>
                          {server.command}
                          {server.args && server.args.length > 0 && ` ${server.args.join(' ')}`}
                        </span>
                      )}
                      {(server.type === 'sse' || server.type === 'http') && server.url && (
                        <span className={styles.listItemMeta}>{server.url}</span>
                      )}
                      {server.env && Object.keys(server.env).length > 0 && (
                        <span className={styles.listItemMeta}>
                          {Object.keys(server.env).length} env var(s)
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {showMcpPicker && !showMcpForm && (
                <div className={styles.pickerPanel}>
                  <div className={styles.pickerList}>
                    {availableMcpServers
                      .filter(s => !state.mcpServers.some(ms => ms.name === s.name))
                      .map(server => (
                        <button
                          key={server.name}
                          className={styles.pickerItem}
                          onClick={() => handleAddMcpServer(server)}
                          type="button"
                        >
                          <Server className={styles.pickerItemIcon} />
                          {server.name}
                          <span className={styles.mcpTypeBadge}>{server.type}</span>
                        </button>
                      ))}
                  </div>
                  <div className={styles.pickerActions}>
                    <button
                      className={styles.smallButton}
                      onClick={() => setShowMcpForm(true)}
                      type="button"
                    >
                      <Plus className={styles.addIcon} />
                      Add Custom
                    </button>
                    <button
                      className={styles.smallButtonMuted}
                      onClick={resetMcpForm}
                      type="button"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {showMcpForm && (
                <div className={styles.pickerPanel}>
                  <div className={styles.pickerCustom}>
                    <input
                      className={styles.formInput}
                      placeholder="Server name (required)"
                      value={customMcpName}
                      onChange={e => setCustomMcpName(e.target.value)}
                    />

                    <div className={styles.mcpTypeGroup}>
                      {(['stdio', 'sse', 'http'] as const).map(t => (
                        <button
                          key={t}
                          className={cn(
                            styles.mcpTypeButton,
                            customMcpType === t && styles.mcpTypeButtonActive
                          )}
                          onClick={() => setCustomMcpType(t)}
                          type="button"
                        >
                          {t}
                        </button>
                      ))}
                    </div>

                    {customMcpType === 'stdio' && (
                      <>
                        <input
                          className={styles.formInput}
                          placeholder="Command (e.g. mcp-server-github)"
                          value={customMcpCommand}
                          onChange={e => setCustomMcpCommand(e.target.value)}
                        />
                        <input
                          className={styles.formInput}
                          placeholder="Args (space-separated)"
                          value={customMcpArgs}
                          onChange={e => setCustomMcpArgs(e.target.value)}
                        />
                      </>
                    )}

                    {(customMcpType === 'sse' || customMcpType === 'http') && (
                      <input
                        className={styles.formInput}
                        placeholder="URL (e.g. http://localhost:3000/sse)"
                        value={customMcpUrl}
                        onChange={e => setCustomMcpUrl(e.target.value)}
                      />
                    )}

                    {/* Environment Variables */}
                    <span className={styles.nestedLabel}>Environment Variables</span>
                    {Object.entries(customMcpEnv).length > 0 && (
                      <div className={styles.itemList}>
                        {Object.entries(customMcpEnv).map(([key, val]) => (
                          <div key={key} className={styles.listItem}>
                            <span className={styles.envPair}>
                              <span className={styles.envKey}>{key}</span>
                              <span className={styles.envVal}>{val}</span>
                            </span>
                            <button
                              className={styles.removeButton}
                              onClick={() => {
                                const next = { ...customMcpEnv };
                                delete next[key];
                                setCustomMcpEnv(next);
                              }}
                              type="button"
                              aria-label={`Remove ${key}`}
                            >
                              <X className={styles.removeIcon} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    <div className={styles.addEnvRow}>
                      <input
                        className={styles.formInput}
                        placeholder="Key"
                        value={customMcpEnvKey}
                        onChange={e => setCustomMcpEnvKey(e.target.value)}
                      />
                      <input
                        className={styles.formInput}
                        placeholder="Value"
                        value={customMcpEnvVal}
                        onChange={e => setCustomMcpEnvVal(e.target.value)}
                      />
                      <button
                        className={styles.smallButton}
                        onClick={() => {
                          if (customMcpEnvKey.trim()) {
                            setCustomMcpEnv(prev => ({
                              ...prev,
                              [customMcpEnvKey.trim()]: customMcpEnvVal,
                            }));
                            setCustomMcpEnvKey('');
                            setCustomMcpEnvVal('');
                          }
                        }}
                        type="button"
                        disabled={!customMcpEnvKey.trim()}
                      >
                        Add
                      </button>
                    </div>

                    <div className={styles.pickerActions}>
                      <button
                        className={styles.smallButton}
                        onClick={handleAddCustomMcp}
                        type="button"
                        disabled={!customMcpName.trim()}
                      >
                        Add Server
                      </button>
                      <button
                        className={styles.smallButtonMuted}
                        onClick={resetMcpForm}
                        type="button"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {!showMcpPicker && !showMcpForm && (
                <button
                  className={styles.addButton}
                  onClick={() => setShowMcpPicker(true)}
                  type="button"
                >
                  <Plus className={styles.addIcon} />
                  Add MCP Server
                </button>
              )}
            </div>

            {/* Restrict Terminal Shell */}
            <div className={styles.formGroup}>
              <div className={styles.toggleRow}>
                <label className={styles.formLabel}>
                  <Shield className={styles.formLabelIcon} />
                  Restrict terminal shell
                </label>
                <button
                  className={cn(styles.toggle, state.terminalRestricted && styles.toggleActive)}
                  onClick={handleToggleTerminalRestricted}
                  type="button"
                  role="switch"
                  aria-checked={state.terminalRestricted}
                >
                  <span className={styles.toggleKnob} />
                </button>
              </div>
              <span className={styles.terminalRestrictedHint}>
                When enabled, the session terminal is restricted to allowed commands only
              </span>

              {state.terminalRestricted && (
                <div className={styles.nestedSection}>
                  <span className={styles.nestedLabel}>Allowed Commands</span>
                  {state.template.terminalSidecar.allowedCommands.map((cmd, idx) => (
                    <div key={idx} className={styles.listRow}>
                      <input
                        className={styles.formInput}
                        value={cmd}
                        onChange={e => handleUpdateAllowedCommand(idx, e.target.value)}
                        placeholder="e.g. npm test"
                      />
                      <button
                        className={styles.removeButton}
                        onClick={() => handleRemoveAllowedCommand(idx)}
                        type="button"
                        aria-label="Remove command"
                      >
                        <X className={styles.removeIcon} />
                      </button>
                    </div>
                  ))}
                  <button
                    className={styles.addButton}
                    onClick={handleAddAllowedCommand}
                    type="button"
                  >
                    <Plus className={styles.addIcon} />
                    Add Command
                  </button>
                </div>
              )}
            </div>

            {/* Resource Config */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Resources</label>
              {aggregatedResources.length > 0 ? (
                <>
                  {['compute', 'accelerator'].map(category => {
                    const items = aggregatedResources.filter(
                      ar => ar.resourceType.category === category
                    );
                    if (items.length === 0) return null;
                    return (
                      <div
                        key={category}
                        className={styles.resourceCategory}
                        data-category={category}
                      >
                        <span className={styles.resourceCategoryLabel}>
                          {category === 'compute' ? 'Compute' : 'Accelerator'}
                        </span>
                        <div className={styles.resourceGrid}>
                          {items.map(({ resourceType: rt, totalAvailable }) => {
                            const inputVal = state.resourceConfig[rt.name] ?? '';
                            const error = validateResourceInput(inputVal, rt.unit, totalAvailable);
                            const parsed = inputVal ? parseK8sQuantity(inputVal, rt.unit) : NaN;
                            const showParsed =
                              !isNaN(parsed) && rt.unit === 'bytes' && inputVal.trim() !== '';
                            return (
                              <div key={rt.name} className={styles.resourceField}>
                                <label className={styles.resourceLabel}>
                                  {rt.displayName}
                                  <span className={styles.resourceUnit}>
                                    {rt.unit === 'bytes'
                                      ? 'e.g. 4Gi, 512Mi'
                                      : rt.unit === 'cores'
                                        ? 'e.g. 4, 500m'
                                        : rt.unit}
                                  </span>
                                </label>
                                <input
                                  className={cn(styles.formInput, error && styles.formInputError)}
                                  value={inputVal}
                                  onChange={e =>
                                    onChange({
                                      resourceConfig: {
                                        ...state.resourceConfig,
                                        [rt.name]: e.target.value || undefined,
                                      },
                                    })
                                  }
                                  placeholder={
                                    rt.name === 'memory' || rt.unit === 'bytes'
                                      ? 'e.g. 8Gi'
                                      : rt.name === 'cpu'
                                        ? 'e.g. 4'
                                        : 'e.g. 1'
                                  }
                                />
                                {error ? (
                                  <span className={styles.resourceError}>{error}</span>
                                ) : (
                                  <span
                                    className={styles.resourceCapacity}
                                    data-category={category}
                                  >
                                    {showParsed && `= ${formatHumanBytes(parsed)} · `}
                                    {formatResourceValue(totalAvailable, rt.unit)}{' '}
                                    {rt.unit === 'bytes' ? '' : rt.unit} available
                                  </span>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </>
              ) : (
                <div className={styles.resourceGrid}>
                  <div className={styles.resourceField}>
                    <label className={styles.resourceLabel}>CPU</label>
                    <input
                      className={styles.formInput}
                      value={state.resourceConfig.cpu ?? ''}
                      onChange={e =>
                        onChange({
                          resourceConfig: {
                            ...state.resourceConfig,
                            cpu: e.target.value || undefined,
                          },
                        })
                      }
                      placeholder="e.g. 4"
                    />
                  </div>
                  <div className={styles.resourceField}>
                    <label className={styles.resourceLabel}>Memory</label>
                    <input
                      className={styles.formInput}
                      value={state.resourceConfig.memory ?? ''}
                      onChange={e =>
                        onChange({
                          resourceConfig: {
                            ...state.resourceConfig,
                            memory: e.target.value || undefined,
                          },
                        })
                      }
                      placeholder="e.g. 8Gi"
                    />
                  </div>
                </div>
              )}
              {gpuTypes.length > 0 && (
                <div className={styles.resourceField}>
                  <label className={styles.resourceLabel}>GPU Type</label>
                  <select
                    className={styles.formSelect}
                    value={state.resourceConfig.gpu_type ?? ''}
                    onChange={e =>
                      onChange({
                        resourceConfig: {
                          ...state.resourceConfig,
                          gpu_type: e.target.value || undefined,
                        },
                      })
                    }
                  >
                    <option value="">Any</option>
                    {gpuTypes.map(gt => (
                      <option key={gt.value} value={gt.value}>
                        {gt.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {hasGpuRequested && (
                <div className={styles.toggleRow}>
                  <label className={styles.formLabel}>GPU time-slicing</label>
                  <button
                    className={cn(
                      styles.toggle,
                      state.resourceConfig.gpu_timeslice === 'true' && styles.toggleActive
                    )}
                    onClick={() =>
                      onChange({
                        resourceConfig: {
                          ...state.resourceConfig,
                          gpu_timeslice:
                            state.resourceConfig.gpu_timeslice === 'true' ? undefined : 'true',
                        },
                      })
                    }
                    type="button"
                    role="switch"
                    aria-checked={state.resourceConfig.gpu_timeslice === 'true'}
                  >
                    <span className={styles.toggleKnob} />
                  </button>
                </div>
              )}
              {hasGpuRequested && state.resourceConfig.gpu_timeslice === 'true' && (
                <span className={styles.terminalRestrictedHint}>
                  GPU is shared between the AI broker and your workload via NVIDIA time-slicing
                </span>
              )}
            </div>

            {/* Environment Variables */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Environment Variables</label>
              {Object.entries(state.envVars).length > 0 && (
                <div className={styles.itemList}>
                  {Object.entries(state.envVars).map(([key, val]) => (
                    <div key={key} className={styles.listItem}>
                      <span className={styles.envPair}>
                        <span className={styles.envKey}>{key}</span>
                        <span className={styles.envVal}>{val}</span>
                      </span>
                      <button
                        className={styles.removeButton}
                        onClick={() => handleRemoveEnvVar(key)}
                        type="button"
                        aria-label={`Remove ${key}`}
                      >
                        <X className={styles.removeIcon} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className={styles.addEnvRow}>
                <input
                  className={styles.formInput}
                  placeholder="Key"
                  value={newEnvKey}
                  onChange={e => setNewEnvKey(e.target.value)}
                />
                <input
                  className={styles.formInput}
                  placeholder="Value"
                  value={newEnvVal}
                  onChange={e => setNewEnvVal(e.target.value)}
                />
                <button
                  className={styles.smallButton}
                  onClick={handleAddEnvVar}
                  type="button"
                  disabled={!newEnvKey.trim()}
                >
                  Add
                </button>
              </div>
            </div>

            {/* Credentials */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                <KeyRound className={styles.formLabelIcon} />
                Credentials
              </label>
              {allCredentials.length > 0 ? (
                <div className={styles.secretGrid}>
                  {allCredentials.map(item => (
                    <label key={item.name} className={styles.secretItem}>
                      <input
                        type="checkbox"
                        checked={state.selectedCredentials.includes(item.name)}
                        onChange={() => handleToggleCredential(item.name)}
                        className={styles.secretCheckbox}
                      />
                      <span className={styles.secretName}>{item.name}</span>
                    </label>
                  ))}
                </div>
              ) : (
                <span className={styles.emptyHint}>No credentials available</span>
              )}
            </div>

            {/* Integrations */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>
                <Plug className={styles.formLabelIcon} />
                Select integrations
              </label>
              <span className={styles.formLabelHint}>
                Attach pre-configured integrations to this session
              </span>
              {integrations.filter(i => i.enabled).length > 0 ? (
                <div className={styles.secretGrid}>
                  {integrations
                    .filter(i => i.enabled)
                    .map(integ => (
                      <label key={integ.id} className={styles.secretItem}>
                        <input
                          type="checkbox"
                          checked={state.selectedIntegrations.includes(integ.id)}
                          onChange={() => handleToggleIntegration(integ.id)}
                          className={styles.secretCheckbox}
                        />
                        <span className={styles.secretName}>{integ.slug}</span>
                      </label>
                    ))}
                </div>
              ) : (
                <span className={styles.emptyHint}>
                  No integrations configured. Integrations are configured by your administrator.
                </span>
              )}
            </div>

            {/* Setup Scripts */}
            <div className={styles.formGroup}>
              <label className={styles.formLabel}>Setup Scripts</label>
              {state.setupScripts.map((script, idx) => (
                <div key={idx} className={styles.listRow}>
                  <input
                    className={styles.formInput}
                    value={script}
                    onChange={e => handleUpdateScript(idx, e.target.value)}
                    placeholder="e.g. npm install"
                  />
                  <button
                    className={styles.removeButton}
                    onClick={() => handleRemoveScript(idx)}
                    type="button"
                    aria-label="Remove script"
                  >
                    <X className={styles.removeIcon} />
                  </button>
                </div>
              ))}
              <button className={styles.addButton} onClick={handleAddScript} type="button">
                <Plus className={styles.addIcon} />
                Add Script
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Save as Preset */}
      <div className={styles.savePresetSection}>
        {showSavePreset ? (
          <div className={styles.savePresetForm}>
            <input
              className={styles.formInput}
              placeholder="Preset name"
              value={savePresetName}
              onChange={e => setSavePresetName(e.target.value)}
            />
            <div className={styles.pickerActions}>
              <button
                className={styles.smallButton}
                onClick={handleSavePreset}
                type="button"
                disabled={!savePresetName.trim() || isSavingPreset}
              >
                {isSavingPreset ? 'Saving...' : 'Save'}
              </button>
              <button
                className={styles.smallButtonMuted}
                onClick={() => {
                  setShowSavePreset(false);
                  setSavePresetName('');
                }}
                type="button"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button
            className={styles.savePresetButton}
            onClick={() => setShowSavePreset(true)}
            type="button"
          >
            <Save className={styles.addIcon} />
            Save as Preset
          </button>
        )}
      </div>
    </div>
  );
}
