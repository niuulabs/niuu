import { useState, useEffect, useMemo, useCallback, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import type { BifrostModel, IBifrostService } from '@niuulabs/plugin-bifrost';
import { useService } from '@niuulabs/plugin-sdk';
import {
  BranchSelect,
  Dialog,
  DialogContent,
  Field,
  Input,
  RepoSelect,
  SegmentedFilter,
  type RepoRecord,
  Textarea,
} from '@niuulabs/ui';
import './LaunchWizard.css';
import type { IVolundrService } from '../ports/IVolundrService';
import type {
  ClusterResourceInfo,
  McpServerConfig,
  SessionSource,
  SessionDefinition,
  IntegrationConnection,
  VolundrTarget,
  StoredCredential,
  TrackerIssue,
  VolundrLaunchSpec,
  VolundrWorkspace,
} from '../models/volundr.model';
import { parsePresetYaml, serializePresetYaml } from '../utils/presetYaml';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = 'source' | 'runtime' | 'confirm' | 'booting';

type RuntimeModelDescriptor = Pick<
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

const STEPS: WizardStep[] = ['source', 'runtime', 'confirm'];

const STEP_LABELS: Record<string, string> = {
  source: 'Source',
  runtime: 'Runtime',
  confirm: 'Confirm',
};

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

const FALLBACK_SESSION_DEFINITIONS: SessionDefinition[] = [
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

const BOOT_STEPS = [
  { id: 'schedule', label: 'schedule pod' },
  { id: 'pull', label: 'pull image' },
  { id: 'creds', label: 'check credentials' },
  { id: 'clone', label: 'clone workspace' },
  { id: 'mount', label: 'attach PVCs' },
  { id: 'mcp', label: 'bring MCP servers up' },
  { id: 'cli', label: 'boot CLI tool' },
  { id: 'ready', label: 'ready' },
];

const NEW_WORKSPACE_VALUE = '__new__';
const NO_PRESET_VALUE = '__custom__';
type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};
const SECONDARY_BUTTON_CLASS =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary niuu:hover:border-brand niuu:hover:bg-bg-tertiary';
const MUTED_BUTTON_CLASS =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary niuu:hover:border-brand niuu:hover:bg-bg-tertiary';

function WizardSelect({
  options,
  value,
  onChange,
  placeholder,
  testId,
}: {
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId?: string;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      data-testid={testId}
      aria-label={placeholder}
      className="niuu-form-control niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export interface LaunchWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialLaunchSpecRef?: string;
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

function findSessionDefinition(
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
    workloadConfig: {},
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
    workloadConfig: {},
  };
}

export function hasPresetBackedRuntime(form: WizardForm): boolean {
  return (
    form.mcpServers.length > 0 ||
    form.envVars.some((entry) => entry.key.trim()) ||
    form.setupScripts.some((script) => script.trim())
  );
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

export function StepIndicator({ current, steps }: { current: WizardStep; steps: WizardStep[] }) {
  const idx = steps.indexOf(current);
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:py-4" data-testid="step-indicator">
      {steps.map((step, i) => (
        <div key={step} className="niuu:flex niuu:items-center niuu:gap-2">
          <div
            className={`niuu:flex niuu:h-6 niuu:w-6 niuu:items-center niuu:justify-center niuu:rounded-full niuu:font-mono niuu:text-xs ${
              i < idx
                ? 'niuu:bg-brand niuu:text-bg-primary'
                : i === idx
                  ? 'niuu:border-2 niuu:border-brand niuu:text-brand'
                  : 'niuu:border niuu:border-border-subtle niuu:text-text-faint'
            }`}
            data-testid={`step-${step}`}
          >
            {i < idx ? '\u2713' : i + 1}
          </div>
          <span
            className={`niuu:text-xs ${i === idx ? 'niuu:text-text-primary' : 'niuu:text-text-faint'}`}
          >
            {STEP_LABELS[step]}
          </span>
          {i < steps.length - 1 && (
            <div
              className={`niuu:h-px niuu:w-8 ${i < idx ? 'niuu:bg-brand' : 'niuu:bg-border-subtle'}`}
            />
          )}
        </div>
      ))}
    </div>
  );
}

export function SectionCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
      <div className="niuu:mb-4 niuu:border-b niuu:border-border-subtle niuu:pb-3">
        <h3 className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{title}</h3>
        {description ? (
          <p className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">{description}</p>
        ) : null}
      </div>
      <div className="niuu:flex niuu:flex-col niuu:gap-4">{children}</div>
    </section>
  );
}

export function RuntimePanel({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-4">
      <div className="niuu:mb-3">
        <div className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{title}</div>
        {description ? (
          <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">{description}</div>
        ) : null}
      </div>
      <div className="niuu:flex niuu:flex-col niuu:gap-4">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Source
// ---------------------------------------------------------------------------

export function SourceStep({
  form,
  update,
  repos,
  branchOptions,
  trackerResults,
  trackerLoading,
}: {
  form: WizardForm;
  update: (patch: Partial<WizardForm>) => void;
  repos: RepoRecord[];
  branchOptions: string[];
  trackerResults: TrackerIssue[];
  trackerLoading: boolean;
}) {
  const currentRepo = repos.find((repo) => repo.cloneUrl === form.repo);

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-source-content">
      <SectionCard
        title="Workspace source"
        description="Choose where the session should start from and attach tracker context if needed."
      >
        <div className="niuu:flex niuu:gap-2">
          {(['git', 'local_mount', 'blank'] as const).map((t) => (
            <button
              key={t}
              className={`niuu:rounded-md niuu:border niuu:px-3 niuu:py-2 niuu:text-xs ${
                form.sourcetype === t
                  ? 'niuu:border-brand niuu:bg-bg-tertiary niuu:text-text-primary'
                  : 'niuu:border-border-subtle niuu:bg-bg-primary niuu:text-text-secondary niuu:hover:border-brand'
              }`}
              onClick={() => update({ sourcetype: t })}
              data-testid={`source-tab-${t}`}
            >
              {t === 'local_mount' ? 'local mount' : t}
            </button>
          ))}
        </div>
        {form.sourcetype === 'git' ? (
          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
            <Field label="Repository">
              {repos.length > 0 ? (
                <RepoSelect
                  repos={repos}
                  value={form.repo}
                  onChange={(value: string) => {
                    const repo = repos.find((item) => item.cloneUrl === value);
                    update({
                      repo: value,
                      branch: repo?.defaultBranch ?? '',
                      workspaceId: '',
                    });
                  }}
                  placeholder="Select repository"
                  testId="repo-select"
                />
              ) : (
                <Input
                  value={form.repo}
                  onChange={(e) => update({ repo: e.target.value, workspaceId: '' })}
                  placeholder="github.com/niuulabs/volundr"
                />
              )}
            </Field>
            <Field label="Branch">
              {branchOptions.length ? (
                currentRepo?.branches.length ? (
                  <BranchSelect
                    repos={repos}
                    selectedRepos={form.repo}
                    value={form.branch}
                    onChange={(value: string) => update({ branch: value })}
                    placeholder="Select branch"
                    testId="branch-select"
                  />
                ) : (
                  <WizardSelect
                    options={branchOptions.map((branch) => ({ value: branch, label: branch }))}
                    value={form.branch}
                    onChange={(value) => update({ branch: value })}
                    placeholder="Select branch"
                    testId="branch-select"
                  />
                )
              ) : (
                <Input
                  value={form.branch}
                  onChange={(e) => update({ branch: e.target.value })}
                  placeholder="main"
                />
              )}
            </Field>
          </div>
        ) : null}
        {form.sourcetype === 'local_mount' ? (
          <Field label="Path">
            <Input
              value={form.mountPath}
              onChange={(e) => update({ mountPath: e.target.value })}
              placeholder="~/code/niuu"
            />
          </Field>
        ) : null}
        {form.sourcetype === 'blank' ? (
          <p className="niuu:font-mono niuu:text-xs niuu:text-text-faint">
            Pod will boot with empty /workspace
          </p>
        ) : null}
        <Field label="Session name (optional)">
          <Input
            value={form.sessionName}
            onChange={(e) => update({ sessionName: e.target.value })}
            placeholder="auto-generated from branch if blank"
          />
        </Field>
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          <Field label="Tracker issue (optional)">
            <Input
              value={form.trackerQuery}
              onChange={(e) => update({ trackerQuery: e.target.value })}
              placeholder="Search tracker issues"
            />
          </Field>
          {form.trackerIssue ? (
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-secondary">
              linked: <span className="niuu:font-mono">{form.trackerIssue.identifier}</span> ·{' '}
              {form.trackerIssue.title}
              <button
                type="button"
                className="niuu:ml-3 niuu:text-text-faint niuu:hover:text-text-primary"
                onClick={() => update({ trackerIssue: null, trackerQuery: '' })}
              >
                clear
              </button>
            </div>
          ) : null}
          {trackerLoading ? (
            <div className="niuu:text-xs niuu:text-text-faint">searching…</div>
          ) : trackerResults.length > 0 ? (
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
              {trackerResults.slice(0, 6).map((issue) => (
                <button
                  key={issue.id}
                  type="button"
                  className={`${SECONDARY_BUTTON_CLASS} niuu:text-left`}
                  onClick={() => update({ trackerIssue: issue, trackerQuery: issue.identifier })}
                >
                  <div className="niuu:font-mono niuu:text-text-primary">{issue.identifier}</div>
                  <div className="niuu:text-text-muted">{issue.title}</div>
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </SectionCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Runtime
// ---------------------------------------------------------------------------

export function RuntimeStep({
  form,
  update,
  models,
  workspaces,
  targets,
  credentials,
  integrations,
  clusterResources,
  presets,
  selectedPreset,
  availableMcpServers,
  sessionDefinitions,
  onApplyPreset,
  onSavePreset,
}: {
  form: WizardForm;
  update: (patch: Partial<WizardForm>) => void;
  models: Record<string, RuntimeModelDescriptor>;
  workspaces: VolundrWorkspace[];
  targets: VolundrTarget[];
  credentials: StoredCredential[];
  integrations: IntegrationConnection[];
  clusterResources: ClusterResourceInfo | null;
  presets: VolundrLaunchSpec[];
  selectedPreset: VolundrLaunchSpec | null;
  availableMcpServers: McpServerConfig[];
  sessionDefinitions: SessionDefinition[];
  onApplyPreset: (launchSpecRef: string) => void;
  onSavePreset: (name: string) => Promise<void>;
}) {
  const compatibleModels = filterModelsForDefinition(models, form.definition, sessionDefinitions);
  const modelOptions = Object.entries(compatibleModels).map(([id, model]) => ({
    value: id,
    label: formatModelOption(id, model),
  }));
  const selectedDefinition =
    findSessionDefinition(form.definition, sessionDefinitions)?.displayName ?? form.definition;
  const totalModelCount = Object.keys(models).length;
  const targetTagOptions = getTargetTagOptions(targets);
  const matchingTagTargets = getMatchingTargets(targets, form.targetTags, form.targetMatch);
  const filteredWorkspaces = workspaces.filter((workspace) => {
    if (form.sourcetype !== 'git' || !form.repo.trim() || !workspace.sourceUrl) return true;
    return normalizeRepoUrl(workspace.sourceUrl) === normalizeRepoUrl(form.repo);
  });
  const workspaceOptions = [
    { value: NEW_WORKSPACE_VALUE, label: 'New workspace' },
    ...filteredWorkspaces.map((workspace) => ({
      value: workspace.id,
      label: workspaceLabel(workspace),
    })),
  ];
  const resourceCapacities = useMemo(
    () => aggregateResourceCapacity(clusterResources),
    [clusterResources],
  );
  const resourceErrors = useMemo(
    () => getResourceErrors(form, clusterResources),
    [form, clusterResources],
  );
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [presetName, setPresetName] = useState('');
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [showCustomMcp, setShowCustomMcp] = useState(false);
  const [customMcpName, setCustomMcpName] = useState('');
  const [customMcpType, setCustomMcpType] = useState<McpServerConfig['type']>('stdio');
  const [customMcpCommand, setCustomMcpCommand] = useState('');
  const [customMcpArgs, setCustomMcpArgs] = useState('');
  const [customMcpUrl, setCustomMcpUrl] = useState('');
  const [customMcpEnvKey, setCustomMcpEnvKey] = useState('');
  const [customMcpEnvValue, setCustomMcpEnvValue] = useState('');
  const [customMcpEnv, setCustomMcpEnv] = useState<Record<string, string>>({});
  const selectedMcpNames = new Set(form.mcpServers.map((server) => server.name));
  const availablePresetServers = availableMcpServers.filter(
    (server) => !selectedMcpNames.has(server.name),
  );

  const resetCustomMcp = useCallback(() => {
    setShowCustomMcp(false);
    setCustomMcpName('');
    setCustomMcpType('stdio');
    setCustomMcpCommand('');
    setCustomMcpArgs('');
    setCustomMcpUrl('');
    setCustomMcpEnvKey('');
    setCustomMcpEnvValue('');
    setCustomMcpEnv({});
  }, []);

  const handleToggleYaml = useCallback(() => {
    if (!form.yamlMode) {
      update({
        yamlMode: true,
        yamlContent: serializePresetYaml(buildYamlRuntimeFields(form)),
      });
      setYamlError(null);
      return;
    }

    try {
      const parsed = parsePresetYaml(form.yamlContent);
      const patch: Partial<WizardForm> = { yamlMode: false };

      if (parsed.cliTool) patch.definition = `skuld-${parsed.cliTool}`;
      if (parsed.model !== undefined) patch.model = parsed.model;
      if (parsed.systemPrompt !== undefined) patch.systemPrompt = parsed.systemPrompt;
      if (parsed.resourceConfig) {
        patch.cpu = parsed.resourceConfig.cpu ?? form.cpu;
        patch.mem = parsed.resourceConfig.memory ?? form.mem;
        patch.gpu = parsed.resourceConfig.gpu ?? form.gpu;
      }
      if (parsed.mcpServers) patch.mcpServers = parsed.mcpServers;
      if (parsed.envVars) {
        patch.envVars = Object.entries(parsed.envVars).map(([key, value]) => ({ key, value }));
      }
      if (parsed.envSecretRefs) patch.selectedCredentials = parsed.envSecretRefs;
      if (parsed.integrationIds) patch.selectedIntegrations = parsed.integrationIds;
      if (parsed.setupScripts) patch.setupScripts = parsed.setupScripts;
      if (parsed.source !== undefined) {
        if (parsed.source === null) {
          patch.sourcetype = 'blank';
          patch.repo = '';
          patch.branch = '';
          patch.mountPath = form.mountPath;
        } else if (parsed.source.type === 'git') {
          patch.sourcetype = 'git';
          patch.repo = parsed.source.repo;
          patch.branch = parsed.source.branch;
        } else {
          patch.sourcetype = 'local_mount';
          patch.mountPath =
            parsed.source.local_path ?? parsed.source.paths[0]?.host_path ?? form.mountPath;
        }
      }

      update(patch);
      setYamlError(null);
    } catch (error) {
      setYamlError(error instanceof Error ? error.message : 'Invalid YAML');
    }
  }, [form, update]);

  const handleAddCustomMcp = useCallback(() => {
    const server: McpServerConfig = {
      name: customMcpName.trim(),
      type: customMcpType,
      ...(customMcpType === 'stdio'
        ? {
            command: customMcpCommand.trim(),
            args: customMcpArgs.trim() ? customMcpArgs.trim().split(/\s+/) : [],
          }
        : {
            url: customMcpUrl.trim(),
          }),
      ...(Object.keys(customMcpEnv).length > 0 ? { env: customMcpEnv } : {}),
    };

    if (!server.name) return;
    update({
      mcpServers: [...form.mcpServers.filter((item) => item.name !== server.name), server],
    });
    resetCustomMcp();
  }, [
    customMcpArgs,
    customMcpCommand,
    customMcpEnv,
    customMcpName,
    customMcpType,
    customMcpUrl,
    form.mcpServers,
    resetCustomMcp,
    update,
  ]);

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-runtime-content">
      {presets.length > 0 ? (
        <SectionCard
          title="Launch spec"
          description="Load catalog specs or save reusable forge configurations."
        >
          <div className="niuu:grid niuu:grid-cols-[2fr_1fr] niuu:gap-4">
            <Field label="Load launch spec">
              <WizardSelect
                options={[
                  { value: NO_PRESET_VALUE, label: 'Custom launch' },
                  ...presets.map((preset) => ({
                    value: launchSpecRef(preset),
                    label: launchSpecLabel(preset),
                  })),
                ]}
                value={form.presetId || NO_PRESET_VALUE}
                onChange={(value) => onApplyPreset(value === NO_PRESET_VALUE ? '' : value)}
              />
            </Field>
            <div className="niuu:flex niuu:items-end niuu:gap-2">
              <Input
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="save as launch spec"
              />
              <button
                type="button"
                className={SECONDARY_BUTTON_CLASS}
                onClick={() => {
                  if (!presetName.trim()) return;
                  void onSavePreset(presetName.trim()).then(() => setPresetName(''));
                }}
              >
                save
              </button>
            </div>
          </div>
          {selectedPreset ? (
            <div className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-faint">
              loaded{' '}
              <span className="niuu:font-mono niuu:text-text-primary">{selectedPreset.name}</span>
              <span> · {selectedPreset.scope === 'system' ? 'catalog' : 'saved'}</span>
              {selectedPreset.description ? ` · ${selectedPreset.description}` : ''}
            </div>
          ) : (
            <div className="niuu:rounded-lg niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-faint">
              No launch spec loaded. Advanced runtime values will be materialized into a saved
              launch spec at launch if needed.
            </div>
          )}
        </SectionCard>
      ) : null}

      <div className="niuu:grid niuu:grid-cols-[1.2fr_0.8fr] niuu:gap-6">
        <SectionCard
          title="Runtime"
          description="Choose the CLI agent, model, workspace, and launch prompts."
        >
          <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
            {sessionDefinitions.map((def) => (
              <button
                key={def.key}
                className={`niuu:flex niuu:items-center niuu:gap-1.5 niuu:rounded-md niuu:border niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary ${
                  form.definition === def.key
                    ? 'niuu:border-brand niuu:bg-bg-tertiary'
                    : 'niuu:border-border-subtle niuu:bg-bg-primary niuu:hover:border-brand niuu:hover:bg-bg-tertiary'
                }`}
                onClick={() => {
                  const patch: Partial<WizardForm> = { definition: def.key };
                  const defaultModel = pickDefaultModelForDefinition(
                    models,
                    def.key,
                    sessionDefinitions,
                  );
                  if (defaultModel) {
                    patch.model = defaultModel;
                  }
                  update(patch);
                }}
                data-testid={`cli-option-${deriveCliTool(def.key)}`}
                title={def.description || undefined}
              >
                <span className="niuu:font-mono niuu:text-base">{getDefinitionRune(def.key)}</span>
                <span className="niuu:font-mono">{def.displayName}</span>
              </button>
            ))}
          </div>
          <div className="niuu:grid niuu:grid-cols-1 niuu:gap-4">
            <Field label="Model">
              {modelOptions.length > 0 ? (
                <WizardSelect
                  options={modelOptions}
                  value={form.model}
                  onChange={(value) => update({ model: value })}
                  placeholder="Select model"
                  testId="model-select"
                />
              ) : (
                <Input
                  value={form.model}
                  onChange={(e) => update({ model: e.target.value })}
                  placeholder="sonnet-primary"
                />
              )}
              {totalModelCount > 0 ? (
                <div className="niuu:text-xs niuu:text-text-faint">
                  {modelOptions.length === totalModelCount
                    ? `Showing all ${totalModelCount} Bifrost models for ${selectedDefinition}.`
                    : `Showing ${modelOptions.length} of ${totalModelCount} Bifrost models compatible with ${selectedDefinition}.`}
                </div>
              ) : null}
            </Field>
          </div>
          {workspaceOptions.length > 1 ? (
            <Field label="Workspace reuse">
              <WizardSelect
                options={workspaceOptions}
                value={form.workspaceId || NEW_WORKSPACE_VALUE}
                onChange={(value) =>
                  update({ workspaceId: value === NEW_WORKSPACE_VALUE ? '' : value })
                }
                testId="workspace-select"
              />
            </Field>
          ) : null}
          {targets.length > 0 ? (
            <Field label="Forge">
              <div className="niuu:space-y-3">
                <SegmentedFilter<WizardForm['targetMode']>
                  aria-label="Forge routing mode"
                  value={form.targetMode}
                  onChange={(targetMode) => update({ targetMode })}
                  options={[
                    { value: 'instance', label: 'Specific Forge' },
                    {
                      value: 'tags',
                      label: 'Match tags',
                      count: targetTagOptions.length,
                      disabled: targetTagOptions.length === 0,
                    },
                  ]}
                />
                {form.targetMode === 'tags' ? (
                  <div className="niuu:space-y-3 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
                    <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                      {targetTagOptions.map((tag) => {
                        const selected = form.targetTags.includes(tag);
                        return (
                          <button
                            key={tag}
                            type="button"
                            className={`niuu:rounded-full niuu:border niuu:px-3 niuu:py-1 niuu:text-xs niuu:font-mono ${
                              selected
                                ? 'niuu:border-brand niuu:bg-bg-tertiary niuu:text-text-primary'
                                : 'niuu:border-border-subtle niuu:bg-bg-secondary niuu:text-text-faint niuu:hover:border-brand'
                            }`}
                            onClick={() =>
                              update({
                                targetTags: selected
                                  ? form.targetTags.filter((item) => item !== tag)
                                  : [...form.targetTags, tag],
                              })
                            }
                          >
                            {tag}
                          </button>
                        );
                      })}
                    </div>
                    <WizardSelect
                      options={[
                        { value: 'all', label: 'Match all selected tags' },
                        { value: 'any', label: 'Match any selected tag' },
                      ]}
                      value={form.targetMatch}
                      onChange={(value) => update({ targetMatch: value === 'any' ? 'any' : 'all' })}
                      placeholder="Tag match"
                      testId="forge-target-match-select"
                    />
                    <div className="niuu:text-xs niuu:text-text-faint">
                      {form.targetTags.length === 0
                        ? 'No tags selected; Guild will route to the default enabled Forge.'
                        : `${matchingTagTargets.length} matching Forge${matchingTagTargets.length === 1 ? '' : 's'}: ${
                            matchingTagTargets.map((target) => target.name).join(', ') || 'none'
                          }`}
                    </div>
                  </div>
                ) : (
                  <WizardSelect
                    options={targets.map((target) => ({
                      value: target.id,
                      label: `${target.name}${target.isDefault ? ' (default)' : ''}${
                        target.tags.length ? ` · ${target.tags.join(', ')}` : ''
                      }`,
                    }))}
                    value={form.instanceId}
                    onChange={(value) => update({ instanceId: value })}
                    placeholder="Select forge"
                    testId="forge-target-select"
                  />
                )}
              </div>
            </Field>
          ) : null}
          <RuntimePanel
            title="Prompting"
            description="Carry system instructions and an initial request into the new session."
          >
            <Field label="Initial prompt (optional)">
              <Textarea
                value={form.initialPrompt}
                onChange={(e) => update({ initialPrompt: e.target.value })}
                rows={3}
                placeholder="Kick off the session with a concrete request"
              />
            </Field>
          </RuntimePanel>
        </SectionCard>

        <SectionCard
          title="Resources"
          description="Request runtime capacity with live guardrails from Forge."
        >
          <Field label="CPU (cores)">
            <Input
              value={form.cpu}
              onChange={(e) => update({ cpu: e.target.value })}
              placeholder="2"
            />
            {resourceCapacities.get('cpu') ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">
                available {formatResourceValue(resourceCapacities.get('cpu')!.total, 'cores')}
              </div>
            ) : null}
            {resourceErrors.cpu ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-danger">{resourceErrors.cpu}</div>
            ) : null}
          </Field>
          <Field label="Memory">
            <Input
              value={form.mem}
              onChange={(e) => update({ mem: e.target.value })}
              placeholder="8Gi"
            />
            {resourceCapacities.get('memory') ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">
                available {formatResourceValue(resourceCapacities.get('memory')!.total, 'bytes')}
              </div>
            ) : null}
            {resourceErrors.memory ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-danger">{resourceErrors.memory}</div>
            ) : null}
          </Field>
          <Field label="GPU">
            <Input
              value={form.gpu}
              onChange={(e) => update({ gpu: e.target.value })}
              placeholder="0"
            />
            {resourceCapacities.get('gpu') ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">
                available {formatResourceValue(resourceCapacities.get('gpu')!.total, 'count')}
              </div>
            ) : null}
            {resourceErrors.gpu ? (
              <div className="niuu:mt-1 niuu:text-xs niuu:text-danger">{resourceErrors.gpu}</div>
            ) : null}
          </Field>
          {/* TODO(niu-758): bring cluster selection back once the canonical forge cluster surface is finalized. */}
        </SectionCard>
      </div>

      <SectionCard
        title="Access"
        description="Attach credentials and enabled integrations to the session."
      >
        <div className="niuu:grid niuu:grid-cols-2 niuu:gap-6">
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-secondary">
              Credentials
            </span>
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
              {credentials.map((credential) => (
                <label
                  key={credential.name}
                  className={`vol-launch-wizard__access-option ${
                    form.selectedCredentials.includes(credential.name)
                      ? 'vol-launch-wizard__access-option--checked'
                      : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    className="vol-launch-wizard__access-option-input"
                    checked={form.selectedCredentials.includes(credential.name)}
                    onChange={(event) =>
                      update({
                        selectedCredentials: event.target.checked
                          ? [...form.selectedCredentials, credential.name]
                          : form.selectedCredentials.filter((name) => name !== credential.name),
                      })
                    }
                    aria-label={credential.name}
                  />
                  <span className="vol-launch-wizard__access-option-box" aria-hidden="true">
                    {form.selectedCredentials.includes(credential.name) ? '✓' : ''}
                  </span>
                  <span className="niuu:font-mono">{credential.name}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            <span className="niuu:text-sm niuu:font-medium niuu:text-text-secondary">
              Integrations
            </span>
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
              {integrations.map((integration) => (
                <label
                  key={integration.id}
                  className={`vol-launch-wizard__access-option vol-launch-wizard__access-option--stacked ${
                    form.selectedIntegrations.includes(integration.id)
                      ? 'vol-launch-wizard__access-option--checked'
                      : ''
                  }`}
                >
                  <input
                    type="checkbox"
                    className="vol-launch-wizard__access-option-input"
                    checked={form.selectedIntegrations.includes(integration.id)}
                    onChange={(event) =>
                      update({
                        selectedIntegrations: event.target.checked
                          ? [...form.selectedIntegrations, integration.id]
                          : form.selectedIntegrations.filter((id) => id !== integration.id),
                      })
                    }
                    aria-label={formatIntegrationLabel(integration)}
                  />
                  <span className="vol-launch-wizard__access-option-box" aria-hidden="true">
                    {form.selectedIntegrations.includes(integration.id) ? '✓' : ''}
                  </span>
                  <span className="niuu:flex niuu:flex-col">
                    <span>{formatIntegrationLabel(integration)}</span>
                    {formatIntegrationMeta(integration) ? (
                      <span className="niuu:text-[11px] niuu:text-text-faint">
                        {formatIntegrationMeta(integration)}
                      </span>
                    ) : null}
                  </span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        title="Advanced"
        description="Prompts, MCP wiring, environment variables, and setup scripts."
      >
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <button
            type="button"
            className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
            onClick={() => setShowAdvanced((value) => !value)}
          >
            {showAdvanced ? 'hide advanced' : 'show advanced'}
          </button>
          {showAdvanced ? (
            <button
              type="button"
              className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
              onClick={handleToggleYaml}
            >
              {form.yamlMode ? 'form view' : 'edit as yaml'}
            </button>
          ) : null}
        </div>
        {showAdvanced && form.yamlMode ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-2">
            <Textarea
              value={form.yamlContent}
              onChange={(e) => update({ yamlContent: e.target.value })}
              rows={20}
              spellCheck={false}
              placeholder="Launch spec YAML"
            />
            {yamlError ? <div className="niuu:text-xs niuu:text-danger">{yamlError}</div> : null}
          </div>
        ) : null}
        {showAdvanced && !form.yamlMode ? (
          <div className="niuu:flex niuu:flex-col niuu:gap-6">
            <RuntimePanel
              title="System prompt"
              description="Override the default agent behavior for this run."
            >
              <Textarea
                value={form.systemPrompt}
                onChange={(e) => update({ systemPrompt: e.target.value })}
                rows={5}
                placeholder="Override the default system prompt"
              />
            </RuntimePanel>

            <RuntimePanel
              title="MCP servers"
              description="Attach launch-spec tools and custom MCP definitions."
            >
              <div className="niuu:flex niuu:flex-col niuu:gap-2">
                {form.mcpServers.length > 0 ? (
                  <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
                    {form.mcpServers.map((server) => (
                      <div
                        key={server.name}
                        className="niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs"
                      >
                        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-2">
                          <span className="niuu:font-mono niuu:text-text-primary">
                            {server.name}
                          </span>
                          <button
                            type="button"
                            className="niuu:text-text-faint niuu:hover:text-text-primary"
                            onClick={() =>
                              update({
                                mcpServers: form.mcpServers.filter(
                                  (item) => item.name !== server.name,
                                ),
                              })
                            }
                          >
                            remove
                          </button>
                        </div>
                        <span className="niuu:text-text-faint">
                          {server.type === 'stdio'
                            ? [server.command, ...(server.args ?? [])].filter(Boolean).join(' ')
                            : (server.url ?? server.type)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3 niuu:text-xs niuu:text-text-faint">
                    No MCP servers selected.
                  </div>
                )}
                {availablePresetServers.length > 0 ? (
                  <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
                    {availablePresetServers.map((server) => (
                      <button
                        key={server.name}
                        type="button"
                        className={`${SECONDARY_BUTTON_CLASS} niuu:text-left`}
                        onClick={() => update({ mcpServers: [...form.mcpServers, server] })}
                      >
                        <div className="niuu:font-mono niuu:text-text-primary">{server.name}</div>
                        <div className="niuu:text-text-faint">{server.type}</div>
                      </button>
                    ))}
                  </div>
                ) : null}
                <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                  <button
                    type="button"
                    className={SECONDARY_BUTTON_CLASS}
                    onClick={() => setShowCustomMcp((value) => !value)}
                  >
                    {showCustomMcp ? 'cancel custom server' : 'add custom server'}
                  </button>
                </div>
                {showCustomMcp ? (
                  <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
                    <Field label="Name">
                      <Input
                        value={customMcpName}
                        onChange={(e) => setCustomMcpName(e.target.value)}
                        placeholder="filesystem"
                      />
                    </Field>
                    <Field label="Type">
                      <WizardSelect
                        options={[
                          { value: 'stdio', label: 'stdio' },
                          { value: 'sse', label: 'sse' },
                          { value: 'http', label: 'http' },
                        ]}
                        value={customMcpType}
                        onChange={(value) => setCustomMcpType(value as McpServerConfig['type'])}
                      />
                    </Field>
                    {customMcpType === 'stdio' ? (
                      <>
                        <Field label="Command">
                          <Input
                            value={customMcpCommand}
                            onChange={(e) => setCustomMcpCommand(e.target.value)}
                            placeholder="uvx"
                          />
                        </Field>
                        <Field label="Args">
                          <Input
                            value={customMcpArgs}
                            onChange={(e) => setCustomMcpArgs(e.target.value)}
                            placeholder="mcp-filesystem /workspace"
                          />
                        </Field>
                      </>
                    ) : (
                      <Field label="URL">
                        <Input
                          value={customMcpUrl}
                          onChange={(e) => setCustomMcpUrl(e.target.value)}
                          placeholder="http://localhost:3000/sse"
                        />
                      </Field>
                    )}
                    <div className="niuu:col-span-2 niuu:flex niuu:flex-col niuu:gap-2">
                      <span className="niuu:text-xs niuu:text-text-faint">Custom environment</span>
                      {Object.entries(customMcpEnv).map(([key, value]) => (
                        <div
                          key={key}
                          className="niuu:grid niuu:grid-cols-[1fr_1fr_auto] niuu:gap-2"
                        >
                          <Input value={key} readOnly />
                          <Input value={value} readOnly />
                          <button
                            type="button"
                            className={MUTED_BUTTON_CLASS}
                            onClick={() => {
                              const next = { ...customMcpEnv };
                              delete next[key];
                              setCustomMcpEnv(next);
                            }}
                          >
                            remove
                          </button>
                        </div>
                      ))}
                      <div className="niuu:grid niuu:grid-cols-[1fr_1fr_auto] niuu:gap-2">
                        <Input
                          value={customMcpEnvKey}
                          onChange={(e) => setCustomMcpEnvKey(e.target.value)}
                          placeholder="KEY"
                        />
                        <Input
                          value={customMcpEnvValue}
                          onChange={(e) => setCustomMcpEnvValue(e.target.value)}
                          placeholder="value"
                        />
                        <button
                          type="button"
                          className={MUTED_BUTTON_CLASS}
                          onClick={() => {
                            if (!customMcpEnvKey.trim()) return;
                            setCustomMcpEnv((current) => ({
                              ...current,
                              [customMcpEnvKey.trim()]: customMcpEnvValue,
                            }));
                            setCustomMcpEnvKey('');
                            setCustomMcpEnvValue('');
                          }}
                        >
                          add
                        </button>
                      </div>
                      <div className="niuu:flex niuu:gap-2">
                        <button
                          type="button"
                          className={MUTED_BUTTON_CLASS}
                          onClick={handleAddCustomMcp}
                          disabled={
                            !customMcpName.trim() ||
                            (customMcpType === 'stdio'
                              ? !customMcpCommand.trim()
                              : !customMcpUrl.trim())
                          }
                        >
                          add server
                        </button>
                        <button
                          type="button"
                          className={MUTED_BUTTON_CLASS}
                          onClick={resetCustomMcp}
                        >
                          reset
                        </button>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </RuntimePanel>

            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-6">
              <RuntimePanel
                title="Environment variables"
                description="Inline env overrides for the launched session."
              >
                {form.envVars.length === 0 ? (
                  <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3 niuu:text-xs niuu:text-text-faint">
                    No environment variables yet. Add one below.
                  </div>
                ) : null}
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {form.envVars.map((entry, index) => (
                    <div
                      key={`${entry.key}-${index}`}
                      className="niuu:grid niuu:grid-cols-[1fr_1fr_auto] niuu:gap-2"
                    >
                      <Input
                        value={entry.key}
                        onChange={(e) =>
                          update({
                            envVars: form.envVars.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, key: e.target.value } : item,
                            ),
                          })
                        }
                        placeholder="KEY"
                      />
                      <Input
                        value={entry.value}
                        onChange={(e) =>
                          update({
                            envVars: form.envVars.map((item, itemIndex) =>
                              itemIndex === index ? { ...item, value: e.target.value } : item,
                            ),
                          })
                        }
                        placeholder="value"
                      />
                      <button
                        type="button"
                        className={SECONDARY_BUTTON_CLASS}
                        onClick={() =>
                          update({
                            envVars: form.envVars.filter((_, itemIndex) => itemIndex !== index),
                          })
                        }
                      >
                        remove
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
                  onClick={() => update({ envVars: [...form.envVars, { key: '', value: '' }] })}
                >
                  add env var
                </button>
              </RuntimePanel>
              <RuntimePanel
                title="Setup scripts"
                description="Commands to run before the first prompt hits the pod."
              >
                {form.setupScripts.length === 0 ? (
                  <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3 niuu:text-xs niuu:text-text-faint">
                    No setup scripts yet. Add one below.
                  </div>
                ) : null}
                <div className="niuu:flex niuu:flex-col niuu:gap-2">
                  {form.setupScripts.map((script, index) => (
                    <div
                      key={`${index}-${script}`}
                      className="niuu:grid niuu:grid-cols-[1fr_auto] niuu:gap-2"
                    >
                      <Input
                        value={script}
                        onChange={(e) =>
                          update({
                            setupScripts: form.setupScripts.map((item, itemIndex) =>
                              itemIndex === index ? e.target.value : item,
                            ),
                          })
                        }
                        placeholder="pnpm install"
                      />
                      <button
                        type="button"
                        className={SECONDARY_BUTTON_CLASS}
                        onClick={() =>
                          update({
                            setupScripts: form.setupScripts.filter(
                              (_, itemIndex) => itemIndex !== index,
                            ),
                          })
                        }
                      >
                        remove
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className={`niuu:self-start ${SECONDARY_BUTTON_CLASS}`}
                  onClick={() => update({ setupScripts: [...form.setupScripts, ''] })}
                >
                  add script
                </button>
              </RuntimePanel>
            </div>
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Confirm
// ---------------------------------------------------------------------------

export function ConfirmStep({
  form,
  models,
  integrations,
  sessionDefinitions,
  targets,
}: {
  form: WizardForm;
  models: Record<string, RuntimeModelDescriptor>;
  integrations: IntegrationConnection[];
  sessionDefinitions: SessionDefinition[];
  targets: VolundrTarget[];
}) {
  const modelLabel = formatModelOption(form.model, models[form.model]);
  const definitionLabel =
    sessionDefinitions.find((d) => d.key === form.definition)?.displayName ?? form.definition;
  const targetLabel =
    form.targetMode === 'tags'
      ? `tags(${form.targetMatch}): ${form.targetTags.join(', ')}`
      : targets.find((target) => target.id === form.instanceId)?.name ||
        form.instanceId ||
        'default';
  const integrationLabels = form.selectedIntegrations.map((id) => {
    const integration = integrations.find((item) => item.id === id);
    return integration ? formatIntegrationLabel(integration) : id;
  });
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-confirm-content">
      <SectionCard
        title="Launch summary"
        description="Final review before Forge provisions the session."
      >
        <div className="niuu:flex niuu:flex-col niuu:divide-y niuu:divide-border-subtle">
          <ConfirmRow label="session" value={deriveSessionName(form)} />
          <ConfirmRow label="forge" value={targetLabel} />
          <ConfirmRow label="definition" value={definitionLabel} />
          <ConfirmRow label="model" value={modelLabel} />
          <ConfirmRow
            label="source"
            value={
              form.sourcetype === 'git'
                ? `${form.repo}@${form.branch}`
                : form.sourcetype === 'local_mount'
                  ? form.mountPath
                  : 'blank'
            }
          />
          <ConfirmRow
            label="resources"
            value={`${form.cpu}c \u00B7 ${form.mem}${form.gpu !== '0' ? ` \u00B7 gpu ${form.gpu}` : ''}`}
          />
          <ConfirmRow label="workspace" value={form.workspaceId || 'new'} />
          <ConfirmRow
            label="tracker"
            value={
              form.trackerIssue
                ? `${form.trackerIssue.identifier} · ${form.trackerIssue.title}`
                : 'none'
            }
          />
        </div>
      </SectionCard>

      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4">
        <SectionCard
          title="Attached access"
          description="Secrets and integrations that will be available immediately."
        >
          <ConfirmChipList
            title="Credentials"
            items={form.selectedCredentials}
            emptyLabel="No credentials attached"
          />
          <ConfirmChipList
            title="Integrations"
            items={integrationLabels}
            emptyLabel="No integrations attached"
          />
        </SectionCard>

        <SectionCard
          title="Advanced runtime"
          description="Additional runtime wiring and bootstrap instructions."
        >
          <ConfirmChipList
            title="MCP servers"
            items={form.mcpServers.map((server) => server.name)}
            emptyLabel="No MCP servers attached"
          />
          <ConfirmChipList
            title="Environment"
            items={form.envVars
              .filter((entry) => entry.key.trim())
              .map((entry) => `${entry.key}=${entry.value}`)}
            emptyLabel="No custom environment variables"
          />
          <ConfirmChipList
            title="Setup scripts"
            items={form.setupScripts.filter((script) => script.trim())}
            emptyLabel="No setup scripts"
          />
          {form.systemPrompt.trim() || form.initialPrompt.trim() ? (
            <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3">
              <div className="niuu:text-xs niuu:text-text-faint">Prompt overrides</div>
              {form.systemPrompt.trim() ? (
                <div className="niuu:mt-2 niuu:text-xs niuu:text-text-secondary">
                  <span className="niuu:font-mono niuu:text-text-primary">system</span> ·{' '}
                  {form.systemPrompt}
                </div>
              ) : null}
              {form.initialPrompt.trim() ? (
                <div className="niuu:mt-2 niuu:text-xs niuu:text-text-secondary">
                  <span className="niuu:font-mono niuu:text-text-primary">initial</span> ·{' '}
                  {form.initialPrompt}
                </div>
              ) : null}
            </div>
          ) : null}
        </SectionCard>
      </div>
    </div>
  );
}

export function ConfirmRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-4 niuu:py-2" data-testid="confirm-row">
      <span className="niuu:w-24 niuu:font-mono niuu:text-xs niuu:text-text-faint">{label}</span>
      <span className="niuu:font-mono niuu:text-sm niuu:text-text-primary">{value}</span>
    </div>
  );
}

export function ConfirmChipList({
  title,
  items,
  emptyLabel,
}: {
  title: string;
  items: string[];
  emptyLabel: string;
}) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-2">
      <div className="niuu:text-xs niuu:text-text-faint">{title}</div>
      {items.length > 0 ? (
        <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
          {items.map((item) => (
            <span
              key={item}
              className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-2.5 niuu:py-1 niuu:font-mono niuu:text-xs niuu:text-text-secondary"
            >
              {item}
            </span>
          ))}
        </div>
      ) : (
        <div className="niuu:text-xs niuu:text-text-faint">{emptyLabel}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Booting
// ---------------------------------------------------------------------------

export function BootingStep({ bootStep, progress }: { bootStep: number; progress: number }) {
  return (
    <div
      className="niuu:flex niuu:flex-col niuu:items-center niuu:gap-6 niuu:py-4"
      data-testid="step-booting-content"
    >
      {/* Anvil SVG */}
      <svg viewBox="0 0 200 80" className="niuu:h-20 niuu:w-48" aria-hidden>
        <rect x="70" y="48" width="60" height="10" rx="1" fill="var(--brand-500)" />
        <rect
          x="80"
          y="58"
          width="40"
          height="8"
          rx="1"
          fill="var(--brand-600, var(--brand-500))"
        />
        <rect
          x="90"
          y="66"
          width="20"
          height="10"
          rx="1"
          fill="var(--brand-700, var(--brand-500))"
        />
        <rect x="92" y="30" width="16" height="18" rx="2" fill="var(--brand-400)" opacity="0.7">
          <animate attributeName="opacity" values="0.6;1;0.7" dur="1.6s" repeatCount="indefinite" />
        </rect>
        <circle cx="70" cy="20" r="1.2" fill="var(--brand-300, var(--brand-500))">
          <animate attributeName="cy" values="20;4;20" dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0;0" dur="2s" repeatCount="indefinite" />
        </circle>
        <circle cx="100" cy="20" r="1.5" fill="var(--brand-400)">
          <animate attributeName="cy" values="20;0;20" dur="1.6s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0;0" dur="1.6s" repeatCount="indefinite" />
        </circle>
        <circle cx="130" cy="20" r="1.2" fill="var(--brand-300, var(--brand-500))">
          <animate attributeName="cy" values="20;6;20" dur="2.4s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="1;0;0" dur="2.4s" repeatCount="indefinite" />
        </circle>
      </svg>
      {/* Progress bar */}
      <div
        className="niuu:w-full niuu:h-1.5 niuu:rounded-full niuu:bg-bg-elevated"
        role="progressbar"
        aria-valuenow={Math.round(progress * 100)}
        aria-valuemax={100}
      >
        <div
          className="niuu:h-full niuu:rounded-full niuu:bg-brand niuu:transition-all"
          style={{ width: `${(progress * 100).toFixed(0)}%` }}
        />
      </div>
      {/* Step list */}
      <div className="niuu:flex niuu:flex-col niuu:gap-2 niuu:w-full">
        {BOOT_STEPS.map((step, i) => (
          <div
            key={step.id}
            className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs ${
              i < bootStep
                ? 'niuu:text-text-muted'
                : i === bootStep
                  ? 'niuu:text-brand'
                  : 'niuu:text-text-faint'
            }`}
            data-testid="boot-step"
          >
            <span className="niuu:w-4 niuu:text-center niuu:font-mono">
              {i < bootStep ? '\u2713' : i === bootStep ? '\u2026' : '\u25CB'}
            </span>
            <span className="niuu:font-mono">{step.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// LaunchWizard
// ---------------------------------------------------------------------------

/** 4-step modal wizard for launching new Volundr sessions. */
export function LaunchWizard({ open, onOpenChange, initialLaunchSpecRef }: LaunchWizardProps) {
  const volundr = useService<IVolundrService>('volundr');
  const bifrost = useService<IBifrostService>('bifrost');
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
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
      },
    );

    return () => {
      cancelled = true;
    };
  }, [bifrost, open, repoCatalog, volundr]);

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
        workloadConfig: {},
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={step === 'booting' ? 'Forging\u2026' : 'Launch pod'}
        className="vol-launch-wizard"
        data-testid="launch-wizard"
      >
        <div className="niuu:flex niuu:flex-col niuu:gap-4 vol-launch-wizard__body">
          {/* Step indicator */}
          {step !== 'booting' && <StepIndicator current={step} steps={STEPS} />}

          {/* Step content */}
          {step === 'source' && (
            <SourceStep
              form={form}
              update={update}
              repos={repos}
              branchOptions={
                repos.find((repo) => repo.cloneUrl === form.repo)?.branches.length
                  ? (repos.find((repo) => repo.cloneUrl === form.repo)?.branches ?? [])
                  : manualBranches
              }
              trackerResults={trackerResults}
              trackerLoading={trackerLoading}
            />
          )}
          {step === 'runtime' && (
            <RuntimeStep
              form={form}
              update={update}
              models={models}
              workspaces={workspaces}
              targets={targets}
              credentials={credentials}
              integrations={integrations}
              clusterResources={clusterResources}
              presets={presets}
              selectedPreset={
                presets.find((preset) => launchSpecRef(preset) === form.presetId) ?? null
              }
              availableMcpServers={availableMcpServers}
              sessionDefinitions={
                sessionDefinitions.length > 0 ? sessionDefinitions : FALLBACK_SESSION_DEFINITIONS
              }
              onApplyPreset={handleApplyPreset}
              onSavePreset={handleSavePreset}
            />
          )}
          {step === 'confirm' && (
            <ConfirmStep
              form={form}
              models={models}
              integrations={integrations}
              sessionDefinitions={
                sessionDefinitions.length > 0 ? sessionDefinitions : FALLBACK_SESSION_DEFINITIONS
              }
              targets={targets}
            />
          )}
          {step === 'booting' && <BootingStep bootStep={bootStep} progress={bootProgress} />}
          {launchError ? (
            <div
              className="niuu:rounded niuu:border niuu:border-danger niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-danger"
              data-testid="wizard-error"
            >
              {launchError}
            </div>
          ) : null}

          {/* Footer */}
          <div className="niuu:flex niuu:items-center niuu:justify-between niuu:pt-4 niuu:border-t niuu:border-border-subtle">
            {canGoBack ? (
              <button
                className="niuu:rounded niuu:px-4 niuu:py-2 niuu:text-sm niuu:text-text-secondary niuu:hover:text-text-primary"
                onClick={handleBack}
                data-testid="wizard-back"
              >
                back
              </button>
            ) : (
              <div />
            )}
            {step === 'booting' ? (
              <button
                className="niuu:py-1 niuu:px-3 niuu:bg-brand niuu:text-bg-primary niuu:border niuu:border-brand niuu:rounded-sm niuu:cursor-pointer niuu:font-mono niuu:text-xs niuu:disabled:opacity-50"
                disabled={bootProgress < 1 || !createdSessionId || launching}
                onClick={() => {
                  if (!createdSessionId) return;
                  onOpenChange(false);
                  void navigate({
                    to: '/volundr/sessions/$sessionId',
                    params: { sessionId: createdSessionId },
                  });
                }}
                data-testid="wizard-open-pod"
              >
                {launching || bootProgress < 1 || !createdSessionId
                  ? 'booting\u2026'
                  : 'open pod \u2192'}
              </button>
            ) : (
              <button
                className="niuu:py-1 niuu:px-3 niuu:bg-brand niuu:text-bg-primary niuu:border niuu:border-brand niuu:rounded-sm niuu:cursor-pointer niuu:font-mono niuu:text-xs niuu:disabled:opacity-50"
                onClick={handleNext}
                disabled={isLastStep && !canLaunch}
                data-testid="wizard-next"
              >
                {isLastStep ? 'forge session' : 'continue \u2192'}
              </button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
