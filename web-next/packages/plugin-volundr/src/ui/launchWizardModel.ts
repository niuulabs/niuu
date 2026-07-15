import type { BifrostModel } from '@niuulabs/plugin-bifrost';
import type {
  ClusterResourceInfo,
  IntegrationConnection,
  McpServerConfig,
  SessionDefinition,
  SessionSource,
  TrackerIssue,
  VolundrLaunchSpec,
  VolundrTarget,
  VolundrWorkspace,
  WorkloadConfig,
} from '../models/volundr.model';

export type WizardStep = 'source' | 'runtime' | 'confirm' | 'booting';

export type RuntimeModelDescriptor = Pick<
  BifrostModel,
  'name' | 'provider' | 'vendor' | 'tier' | 'color' | 'vram' | 'sessionDefinition'
> & {
  cost?: string | number;
  providerKeys?: string[];
};

export interface WizardForm {
  presetId: string;
  sourcetype: 'git' | 'local_mount' | 'blank';
  repo: string;
  branch: string;
  workspaceId: string;
  mountPath: string;
  sessionName: string;
  personaName: string;
  workloadConfig: WorkloadConfig;
  systemPrompt: string;
  initialPrompt: string;
  trackerQuery: string;
  trackerIssue: TrackerIssue | null;
  selectedCredentials: string[];
  selectedIntegrations: string[];
  mcpServers: McpServerConfig[];
  envVars: Array<{ key: string; value: string }>;
  setupScripts: string[];
  definition: string;
  model: string;
  cpu: string;
  mem: string;
  gpu: string;
  cluster: string;
  instanceId: string;
  targetMode: 'instance' | 'tags';
  targetTags: string[];
  targetMatch: 'all' | 'any';
  yamlMode: boolean;
  yamlContent: string;
}

/** Map definition keys to runes for visual branding. */
const DEFINITION_RUNES: Record<string, string> = {
  skuldClaude: '\u16D7',
  skuldClaudeInteractive: '\u16D7',
  skuldCodex: '\u16B2',
  skuldGemini: '\u16C7',
  skuldAider: '\u16A8',
  skuldOpenCode: '\u16A0',
  'skuld-claude': '\u16D7',
  'skuld-claude-interactive': '\u16D7',
  'skuld-codex': '\u16B2',
  'skuld-gemini': '\u16C7',
  'skuld-aider': '\u16A8',
  // Legacy short keys
  claude: '\u16D7',
  codex: '\u16B2',
  gemini: '\u16C7',
  aider: '\u16A8',
};

export const FALLBACK_SESSION_DEFINITIONS: SessionDefinition[] = [
  {
    key: 'skuldClaude',
    displayName: 'Claude Code',
    description: '',
    labels: [],
    defaultModel: '',
    compatibleProviders: ['anthropic'],
  },
  {
    key: 'skuldClaudeInteractive',
    displayName: 'Claude Code Interactive',
    description: '',
    labels: ['interactive'],
    defaultModel: '',
    compatibleProviders: ['anthropic'],
  },
  {
    key: 'skuldCodex',
    displayName: 'Codex',
    description: '',
    labels: [],
    defaultModel: '',
    compatibleProviders: ['openai'],
  },
  {
    key: 'skuldGemini',
    displayName: 'Gemini',
    description: '',
    labels: [],
    defaultModel: '',
    compatibleProviders: ['google'],
  },
  {
    key: 'skuldAider',
    displayName: 'Aider',
    description: '',
    labels: [],
    defaultModel: '',
    compatibleProviders: [],
  },
];

export function getDefinitionRune(key: string): string {
  return DEFINITION_RUNES[key] ?? '\u16A0';
}

export function normalizeDefinitionKey(definitionKey: string): string {
  const normalized = definitionKey.trim();
  const legacyMap: Record<string, string> = {
    claude: 'skuldClaude',
    codex: 'skuldCodex',
    gemini: 'skuldGemini',
    aider: 'skuldAider',
    opencode: 'skuldOpenCode',
    'skuld-claude': 'skuldClaude',
    'skuld-claude-interactive': 'skuldClaudeInteractive',
    'skuld-codex': 'skuldCodex',
    'skuld-gemini': 'skuldGemini',
    'skuld-aider': 'skuldAider',
    'skuld-opencode': 'skuldOpenCode',
  };
  return legacyMap[normalized] ?? normalized;
}

/** Derive a CLI tool name from a definition key for backward compat. */
export function deriveCliTool(definitionKey: string): string {
  const normalized = normalizeDefinitionKey(definitionKey);
  const cliToolMap: Record<string, string> = {
    skuldClaude: 'claude',
    skuldClaudeInteractive: 'claude',
    skuldCodex: 'codex',
    skuldGemini: 'gemini',
    skuldAider: 'aider',
    skuldOpenCode: 'opencode',
  };
  if (normalized in cliToolMap) return cliToolMap[normalized]!;
  if (normalized.startsWith('skuld-')) return normalized.slice('skuld-'.length);
  return normalized;
}

export function definitionToTaskType(definitionKey: string): string {
  const normalized = normalizeDefinitionKey(definitionKey);
  const taskTypeMap: Record<string, string> = {
    skuldClaude: 'skuld-claude',
    skuldClaudeInteractive: 'skuld-claude-interactive',
    skuldCodex: 'skuld-codex',
    skuldGemini: 'skuld-gemini',
    skuldAider: 'skuld-aider',
    skuldOpenCode: 'skuld-opencode',
  };
  return taskTypeMap[normalized] ?? normalized;
}

export function workspaceLabel(workspace: VolundrWorkspace): string {
  if (workspace.sessionName) return workspace.sessionName;
  if (workspace.sourceUrl) {
    const repoName = workspace.sourceUrl.replace(/.*\//, '').replace(/\.git$/, '');
    return `${repoName} / ${workspace.sourceRef ?? 'main'}`;
  }
  return workspace.pvcName;
}

export function normalizeRepoUrl(url: string): string {
  return url
    .replace(/^https?:\/\//, '')
    .replace(/\/$/, '')
    .replace(/\.git$/, '');
}

export function pickDefaultModel(models: Record<string, RuntimeModelDescriptor>): string {
  if ('sonnet-primary' in models) return 'sonnet-primary';
  return Object.keys(models)[0] ?? '';
}

function normalizeModelProvider(value: string | undefined | null): string {
  const provider = String(value ?? '')
    .trim()
    .toLowerCase();
  const aliases: Record<string, string> = {
    anthropic: 'anthropic',
    claude: 'anthropic',
    openai: 'openai',
    codex: 'openai',
    google: 'google',
    gemini: 'google',
    ollama: 'local',
    local: 'local',
  };
  return aliases[provider] ?? provider;
}

export function findSessionDefinition(
  definitionKey: string,
  sessionDefinitions: SessionDefinition[],
): SessionDefinition | null {
  const normalized = normalizeDefinitionKey(definitionKey);
  return (
    sessionDefinitions.find(
      (definition) => normalizeDefinitionKey(definition.key) === normalized,
    ) ?? null
  );
}

function modelProviderTokens(model: RuntimeModelDescriptor): Set<string> {
  return new Set(
    [model.vendor, model.provider, ...(model.providerKeys ?? [])]
      .map(normalizeModelProvider)
      .filter(Boolean),
  );
}

export function isModelCompatibleWithDefinition(
  model: RuntimeModelDescriptor,
  definitionKey: string,
  sessionDefinitions: SessionDefinition[],
): boolean {
  const normalizedDefinition = normalizeDefinitionKey(definitionKey);
  if (
    model.sessionDefinition &&
    normalizeDefinitionKey(model.sessionDefinition) === normalizedDefinition
  ) {
    return true;
  }

  const definition = findSessionDefinition(definitionKey, sessionDefinitions);
  if (!definition) return true;

  const compatibleProviders = (definition.compatibleProviders ?? [])
    .map(normalizeModelProvider)
    .filter(Boolean);
  if (compatibleProviders.length === 0) return true;

  const tokens = modelProviderTokens(model);
  return compatibleProviders.some((provider) => tokens.has(provider));
}

export function filterModelsForDefinition(
  models: Record<string, RuntimeModelDescriptor>,
  definitionKey: string,
  sessionDefinitions: SessionDefinition[],
): Record<string, RuntimeModelDescriptor> {
  return Object.fromEntries(
    Object.entries(models).filter(([, model]) =>
      isModelCompatibleWithDefinition(model, definitionKey, sessionDefinitions),
    ),
  );
}

export function pickDefaultModelForDefinition(
  models: Record<string, RuntimeModelDescriptor>,
  definitionKey: string,
  sessionDefinitions: SessionDefinition[],
): string {
  const definition = findSessionDefinition(definitionKey, sessionDefinitions);
  const compatibleModels = filterModelsForDefinition(models, definitionKey, sessionDefinitions);
  if (definition?.defaultModel && compatibleModels[definition.defaultModel]) {
    return definition.defaultModel;
  }
  return pickDefaultModel(compatibleModels);
}

export function getTargetTagOptions(targets: VolundrTarget[]): string[] {
  return Array.from(new Set(targets.flatMap((target) => target.tags ?? []))).sort((a, b) =>
    a.localeCompare(b),
  );
}

export function targetMatchesTags(
  target: VolundrTarget,
  tags: string[],
  match: 'all' | 'any',
): boolean {
  if (tags.length === 0) return true;
  const have = new Set(target.tags ?? []);
  if (match === 'any') return tags.some((tag) => have.has(tag));
  return tags.every((tag) => have.has(tag));
}

export function getMatchingTargets(
  targets: VolundrTarget[],
  tags: string[],
  match: 'all' | 'any',
): VolundrTarget[] {
  return targets.filter((target) => target.enabled && targetMatchesTags(target, tags, match));
}

export function launchSpecRef(spec: VolundrLaunchSpec): string {
  return spec.id ?? spec.name;
}

export function launchSpecLabel(spec: VolundrLaunchSpec): string {
  const scope = spec.scope === 'system' ? 'catalog' : 'saved';
  return `${spec.name} · ${scope}${spec.isDefault ? ' · default' : ''}`;
}

export function formatModelOption(id: string, model?: RuntimeModelDescriptor): string {
  if (!model) return id;
  const parts = [model.name || id, model.provider];
  if (model.tier) parts.push(model.tier);
  return parts.join(' · ');
}

export function formatIntegrationLabel(integration: IntegrationConnection): string {
  const base = integration.slug
    ? integration.slug.replace(/[-_]+/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
    : integration.id;
  if (integration.credentialName) return `${base} · ${integration.credentialName}`;
  return base;
}

export function formatIntegrationMeta(integration: IntegrationConnection): string | null {
  if (integration.integrationType && integration.credentialName) {
    return `${integration.integrationType.replace(/_/g, ' ')} · ${integration.credentialName}`;
  }
  if (integration.integrationType) return integration.integrationType.replace(/_/g, ' ');
  if (integration.credentialName) return integration.credentialName;
  if (integration.adapter) return integration.adapter;
  return null;
}

export function parseResourceValue(value: string, unit: string): number {
  const trimmed = value.trim();
  if (!trimmed) return Number.NaN;

  if (unit === 'cores') {
    if (trimmed.endsWith('m')) {
      return Number.parseFloat(trimmed.slice(0, -1)) / 1000;
    }
    return Number.parseFloat(trimmed);
  }

  if (unit === 'bytes') {
    const match = trimmed.match(/^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti)?$/i);
    if (!match) return Number.NaN;
    const amount = Number.parseFloat(match[1] ?? '');
    const suffix = (match[2] ?? '').toLowerCase();
    const factors: Record<string, number> = {
      '': 1,
      ki: 1024,
      mi: 1024 ** 2,
      gi: 1024 ** 3,
      ti: 1024 ** 4,
    };
    return amount * (factors[suffix] ?? Number.NaN);
  }

  return Number.parseFloat(trimmed);
}

export function formatResourceValue(value: number, unit: string): string {
  if (!Number.isFinite(value)) return 'unknown';
  if (unit === 'bytes') {
    const gib = value / 1024 ** 3;
    return `${Number.isInteger(gib) ? gib : gib.toFixed(1)}Gi`;
  }
  if (unit === 'cores') {
    return `${Number.isInteger(value) ? value : value.toFixed(1)} cores`;
  }
  return `${Number.isInteger(value) ? value : value.toFixed(1)}`;
}

export function aggregateResourceCapacity(clusterResources: ClusterResourceInfo | null) {
  const totals = new Map<string, { unit: string; total: number; label: string }>();
  if (!clusterResources) return totals;

  for (const resourceType of clusterResources.resourceTypes) {
    let total = 0;
    for (const node of clusterResources.nodes) {
      const raw = node.available[resourceType.resourceKey];
      if (!raw) continue;
      const parsed = parseResourceValue(raw, resourceType.unit);
      if (!Number.isNaN(parsed)) total += parsed;
    }
    totals.set(resourceType.name, {
      unit: resourceType.unit,
      total,
      label: resourceType.displayName,
    });
  }
  return totals;
}

export function getResourceErrors(form: WizardForm, clusterResources: ClusterResourceInfo | null) {
  const capacities = aggregateResourceCapacity(clusterResources);
  const errors: Partial<Record<'cpu' | 'memory' | 'gpu', string>> = {};

  const requests: Array<{ key: 'cpu' | 'memory' | 'gpu'; resourceName: string; value: string }> = [
    { key: 'cpu', resourceName: 'cpu', value: form.cpu },
    { key: 'memory', resourceName: 'memory', value: form.mem },
    { key: 'gpu', resourceName: 'gpu', value: form.gpu === '0' ? '' : form.gpu },
  ];

  for (const request of requests) {
    if (!request.value.trim()) continue;
    const capacity = capacities.get(request.resourceName);
    if (!capacity || capacity.total <= 0) continue;
    const requested = parseResourceValue(request.value, capacity.unit);
    if (Number.isNaN(requested)) {
      errors[request.key] = 'Invalid format';
      continue;
    }
    if (requested > capacity.total) {
      errors[request.key] =
        `Exceeds available capacity (${formatResourceValue(capacity.total, capacity.unit)})`;
    }
  }

  return errors;
}

export function slugifySessionName(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/-{2,}/g, '-')
    .slice(0, 63);
}

export function validateSessionName(name: string): string | null {
  if (!name) return null;
  if (name.length > 63) return 'Session name must be 63 characters or fewer';
  if (/[A-Z]/.test(name)) return 'Session name must be lowercase';
  if (/\s/.test(name)) return 'Session name must not contain spaces';
  if (name.startsWith('-') || name.endsWith('-')) {
    return 'Session name must start and end with a letter or digit';
  }
  if (!/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(name)) {
    return 'Session name may only contain lowercase letters, digits, and hyphens';
  }
  return null;
}

export function deriveSessionName(form: WizardForm): string {
  const explicit = slugifySessionName(form.sessionName);
  if (explicit) return explicit;

  if (form.sourcetype === 'git') {
    const branch = slugifySessionName(form.branch.split('/').at(-1) ?? form.branch);
    if (branch) return branch;
  }

  if (form.sourcetype === 'local_mount') {
    const lastSegment = form.mountPath.split('/').filter(Boolean).at(-1) ?? form.mountPath;
    const mountName = slugifySessionName(lastSegment.replace(/^~/, 'home'));
    if (mountName) return mountName;
  }

  return 'forge-session';
}

export function buildSessionSource(form: WizardForm): SessionSource {
  if (form.sourcetype === 'local_mount') {
    const hostPath = form.mountPath.trim();
    return {
      type: 'local_mount',
      local_path: hostPath,
      paths: hostPath ? [{ host_path: hostPath, mount_path: '/workspace', read_only: false }] : [],
    };
  }

  if (form.sourcetype === 'blank') {
    return {
      type: 'git',
      repo: '',
      branch: '',
    };
  }

  return {
    type: 'git',
    repo: form.repo.trim(),
    branch: form.branch.trim(),
  };
}

export function buildResourceConfig(form: WizardForm): Record<string, string> | undefined {
  const resourceConfig = Object.fromEntries(
    Object.entries({
      cpu: form.cpu.trim(),
      memory: form.mem.trim(),
      gpu: form.gpu.trim() === '0' ? '' : form.gpu.trim(),
    }).filter(([, value]) => value),
  );

  return Object.keys(resourceConfig).length > 0 ? resourceConfig : undefined;
}

export function normalizeEnvVars(
  entries: Array<{ key: string; value: string }>,
): Record<string, string> {
  return Object.fromEntries(
    entries.filter((entry) => entry.key.trim()).map((entry) => [entry.key.trim(), entry.value]),
  );
}

export function buildWorkloadConfig(form: WizardForm): WorkloadConfig {
  const { persona: _persona, ...workloadConfig } = form.workloadConfig ?? {};
  return form.personaName ? { ...workloadConfig, persona: form.personaName } : workloadConfig;
}

export function buildPresetRuntimePayload(
  form: WizardForm,
  presetName?: string,
): Omit<VolundrLaunchSpec, 'id' | 'scope' | 'createdAt' | 'updatedAt'> {
  return {
    name: (presetName ?? form.presetId) || 'launch-preset',
    description: '',
    isDefault: false,
    sessionDefinition: form.definition || null,
    cliTool: deriveCliTool(form.definition) as VolundrLaunchSpec['cliTool'],
    workloadType: definitionToTaskType(form.definition),
    model: form.model || null,
    systemPrompt: form.systemPrompt || null,
    resourceConfig: buildResourceConfig(form) ?? {},
    mcpServers: form.mcpServers,
    terminalSidecar: {
      enabled: false,
      allowedCommands: [],
    },
    skills: [],
    rules: [],
    envVars: normalizeEnvVars(form.envVars),
    envSecretRefs: form.selectedCredentials,
    source:
      form.sourcetype === 'git' && form.repo
        ? { type: 'git', repo: form.repo, branch: form.branch }
        : form.sourcetype === 'local_mount' && form.mountPath.trim()
          ? {
              type: 'local_mount',
              local_path: form.mountPath.trim(),
              paths: [
                { host_path: form.mountPath.trim(), mount_path: '/workspace', read_only: false },
              ],
            }
          : null,
    integrationIds: form.selectedIntegrations,
    repos: [],
    setupScripts: form.setupScripts.filter((script) => script.trim()),
    workspaceLayout: {},
    workloadConfig: buildWorkloadConfig(form),
  };
}

export function buildPresetPayload(
  form: WizardForm,
  presetName: string,
): Omit<VolundrLaunchSpec, 'id' | 'scope' | 'createdAt' | 'updatedAt'> {
  return buildPresetRuntimePayload(form, presetName);
}

export function buildPresetComparisonPayload(
  preset: VolundrLaunchSpec,
): Omit<VolundrLaunchSpec, 'id' | 'scope' | 'createdAt' | 'updatedAt'> {
  return {
    name: preset.name,
    description: preset.description,
    isDefault: preset.isDefault,
    sessionDefinition: preset.sessionDefinition,
    cliTool: preset.cliTool,
    workloadType: preset.workloadType,
    model: preset.model,
    systemPrompt: preset.systemPrompt,
    resourceConfig: preset.resourceConfig,
    mcpServers: preset.mcpServers,
    terminalSidecar: preset.terminalSidecar,
    skills: preset.skills,
    rules: preset.rules,
    envVars: preset.envVars,
    envSecretRefs: preset.envSecretRefs,
    source: preset.source,
    integrationIds: preset.integrationIds,
    repos: preset.repos,
    setupScripts: preset.setupScripts,
    workspaceLayout: preset.workspaceLayout,
    workloadConfig: preset.workloadConfig,
  };
}

export function buildYamlRuntimeFields(form: WizardForm) {
  return {
    cliTool: deriveCliTool(form.definition) as 'claude' | 'codex' | 'gemini' | 'aider',
    workloadType: definitionToTaskType(form.definition),
    model: form.model,
    systemPrompt: form.systemPrompt,
    resourceConfig: buildResourceConfig(form) ?? {},
    mcpServers: form.mcpServers,
    terminalSidecar: {
      enabled: false,
      allowedCommands: [],
    },
    skills: [],
    rules: [],
    envVars: normalizeEnvVars(form.envVars),
    envSecretRefs: form.selectedCredentials,
    source:
      form.sourcetype === 'blank'
        ? null
        : form.sourcetype === 'git'
          ? { type: 'git' as const, repo: form.repo, branch: form.branch }
          : {
              type: 'local_mount' as const,
              local_path: form.mountPath.trim(),
              paths: form.mountPath.trim()
                ? [{ host_path: form.mountPath.trim(), mount_path: '/workspace', read_only: false }]
                : [],
            },
    integrationIds: form.selectedIntegrations,
    setupScripts: form.setupScripts.filter((script) => script.trim()),
    workloadConfig: buildWorkloadConfig(form),
  };
}

export function hasPresetBackedRuntime(form: WizardForm): boolean {
  return (
    form.mcpServers.length > 0 ||
    Object.keys(buildWorkloadConfig(form)).length > 0 ||
    form.envVars.some((entry) => entry.key.trim()) ||
    form.setupScripts.some((script) => script.trim())
  );
}
