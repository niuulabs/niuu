import { describe, expect, it } from 'vitest';
import type {
  ClusterResourceInfo,
  IntegrationConnection,
  VolundrModel,
  VolundrLaunchSpec,
  VolundrTarget,
  VolundrWorkspace,
} from '../models/volundr.model';
import {
  aggregateResourceCapacity,
  buildPresetComparisonPayload,
  buildPresetPayload,
  buildPresetRuntimePayload,
  buildResourceConfig,
  buildSessionSource,
  buildYamlRuntimeFields,
  definitionToTaskType,
  deriveCliTool,
  deriveSessionName,
  filterModelsForDefinition,
  formatIntegrationLabel,
  formatIntegrationMeta,
  formatModelOption,
  formatResourceValue,
  getDefinitionRune,
  getResourceErrors,
  getTargetTagOptions,
  hasPresetBackedRuntime,
  isModelCompatibleWithDefinition,
  getMatchingTargets,
  normalizeDefinitionKey,
  normalizeEnvVars,
  normalizeRepoUrl,
  parseResourceValue,
  pickDefaultModel,
  pickDefaultModelForDefinition,
  slugifySessionName,
  targetMatchesTags,
  validateSessionName,
  workspaceLabel,
  type WizardForm,
} from './LaunchWizard';

function makeForm(overrides: Partial<WizardForm> = {}): WizardForm {
  return {
    presetId: '',
    sourcetype: 'git',
    repo: 'github.com/niuulabs/volundr',
    branch: 'feature/my-work',
    workspaceId: '',
    mountPath: '~/code/niuu',
    sessionName: '',
    personaName: '',
    systemPrompt: '',
    initialPrompt: '',
    trackerQuery: '',
    trackerIssue: null,
    selectedCredentials: [],
    selectedIntegrations: [],
    mcpServers: [],
    envVars: [],
    setupScripts: [],
    definition: 'skuld-claude',
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
    ...overrides,
  };
}

describe('LaunchWizard helpers', () => {
  it('maps definition runes and CLI tool names', () => {
    expect(getDefinitionRune('skuld-codex')).toBe('ᚲ');
    expect(getDefinitionRune('unknown')).toBe('ᚠ');
    expect(deriveCliTool('skuld-gemini')).toBe('gemini');
    expect(deriveCliTool('skuld-claude-interactive')).toBe('claude');
    expect(deriveCliTool('codex')).toBe('codex');
    expect(deriveCliTool('skuld-custom')).toBe('custom');
    expect(deriveCliTool(' bespoke-tool ')).toBe('bespoke-tool');
    expect(normalizeDefinitionKey(' skuld-opencode ')).toBe('skuldOpenCode');
    expect(normalizeDefinitionKey('skuld-claude-interactive')).toBe('skuldClaudeInteractive');
    expect(normalizeDefinitionKey('custom-tool')).toBe('custom-tool');
    expect(definitionToTaskType('skuldClaudeInteractive')).toBe('skuld-claude-interactive');
    expect(definitionToTaskType('skuldOpenCode')).toBe('skuld-opencode');
    expect(definitionToTaskType('raw-task')).toBe('raw-task');
  });

  it('formats workspace and repo metadata', () => {
    const workspace: VolundrWorkspace = {
      id: 'ws-1',
      pvcName: 'workspace-pvc',
      sessionId: null,
      status: 'available',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      sessionName: '',
      sourceUrl: 'https://github.com/niuulabs/volundr.git',
      sourceRef: 'main',
      lastUsedAt: null,
      deletedAt: null,
    };
    expect(workspaceLabel({ ...workspace, sessionName: 'pairing pod' })).toBe('pairing pod');
    expect(workspaceLabel(workspace)).toContain('volundr / main');
    expect(workspaceLabel({ ...workspace, sourceRef: null })).toContain('volundr / main');
    expect(workspaceLabel({ ...workspace, sourceUrl: null })).toBe('workspace-pvc');
    expect(normalizeRepoUrl('https://github.com/niuulabs/volundr.git/')).toBe(
      'github.com/niuulabs/volundr',
    );
  });

  it('picks defaults and formats model/integration labels', () => {
    const models: Record<string, VolundrModel> = {
      'gpt-test': { name: 'GPT Test', provider: 'openai', tier: 'fast' },
      'sonnet-primary': { name: 'Sonnet', provider: 'anthropic', tier: 'smart' },
    };
    expect(pickDefaultModel(models)).toBe('sonnet-primary');
    expect(pickDefaultModel({})).toBe('');
    expect(formatModelOption('gpt-test', models['gpt-test'])).toBe('GPT Test · openai · fast');
    expect(formatModelOption('fallback')).toBe('fallback');
    expect(formatModelOption('provider-only', { provider: 'openai' } as VolundrModel)).toBe(
      'provider-only · openai',
    );

    const integration: IntegrationConnection = {
      id: 'int-1',
      slug: 'github-app',
      credentialName: 'prod-github',
      integrationType: 'source_control',
      adapter: 'github',
      status: 'connected',
    };
    expect(formatIntegrationLabel(integration)).toBe('Github App · prod-github');
    expect(formatIntegrationMeta(integration)).toBe('source control · prod-github');
    expect(formatIntegrationMeta({ ...integration, credentialName: null })).toBe('source control');
    expect(formatIntegrationMeta({ ...integration, integrationType: null })).toBe('prod-github');
    expect(
      formatIntegrationMeta({ ...integration, integrationType: null, credentialName: null }),
    ).toBe('github');
    expect(
      formatIntegrationLabel({ ...integration, slug: null, credentialName: null, id: 'raw-id' }),
    ).toBe('raw-id');
    expect(
      formatIntegrationMeta({
        ...integration,
        integrationType: null,
        credentialName: null,
        adapter: null,
      }),
    ).toBeNull();
  });

  it('filters Bifrost models by selected session runtime', () => {
    const models: Record<string, VolundrModel> = {
      'claude-sonnet': {
        name: 'Claude Sonnet',
        vendor: 'anthropic',
        provider: 'cloud',
        tier: 'balanced',
        sessionDefinition: 'skuldClaude',
      } as VolundrModel,
      'gpt-5.5': {
        name: 'GPT-5.5',
        vendor: 'openai',
        provider: 'cloud',
        tier: 'frontier',
        sessionDefinition: 'skuldCodex',
      } as VolundrModel,
      'llama3.2:latest': {
        name: 'Llama 3.2',
        vendor: 'local',
        provider: 'local',
        tier: 'balanced',
        sessionDefinition: 'skuldOpenCode',
      } as VolundrModel,
    };
    const definitions = [
      {
        key: 'skuldClaude',
        displayName: 'Claude Code',
        description: '',
        labels: [],
        defaultModel: 'claude-sonnet',
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
        key: 'skuldOpenCode',
        displayName: 'OpenCode',
        description: '',
        labels: [],
        defaultModel: '',
        compatibleProviders: [],
      },
    ];

    expect(Object.keys(filterModelsForDefinition(models, 'skuldClaude', definitions))).toEqual([
      'claude-sonnet',
    ]);
    expect(Object.keys(filterModelsForDefinition(models, 'skuldCodex', definitions))).toEqual([
      'gpt-5.5',
    ]);
    expect(Object.keys(filterModelsForDefinition(models, 'skuldOpenCode', definitions))).toEqual([
      'claude-sonnet',
      'gpt-5.5',
      'llama3.2:latest',
    ]);
    expect(isModelCompatibleWithDefinition(models['gpt-5.5']!, 'skuldClaude', definitions)).toBe(
      false,
    );
    expect(pickDefaultModelForDefinition(models, 'skuldClaude', definitions)).toBe('claude-sonnet');
    expect(pickDefaultModelForDefinition(models, 'skuldCodex', definitions)).toBe('gpt-5.5');
  });

  it('matches forge targets by tags', () => {
    const targets: VolundrTarget[] = [
      {
        id: 'forge-alpha',
        slug: 'forge-alpha',
        name: 'Forge Alpha',
        baseUrl: 'https://alpha.example.test',
        enabled: true,
        isDefault: true,
        visibility: 'system',
        tags: ['gpu', 'prod'],
      },
      {
        id: 'forge-beta',
        slug: 'forge-beta',
        name: 'Forge Beta',
        baseUrl: 'https://beta.example.test',
        enabled: true,
        isDefault: false,
        visibility: 'system',
        tags: ['batch'],
      },
    ];

    expect(getTargetTagOptions(targets)).toEqual(['batch', 'gpu', 'prod']);
    expect(targetMatchesTags(targets[0]!, ['gpu', 'prod'], 'all')).toBe(true);
    expect(targetMatchesTags(targets[1]!, ['gpu', 'prod'], 'all')).toBe(false);
    expect(getMatchingTargets(targets, ['gpu', 'batch'], 'any').map((target) => target.id)).toEqual(
      ['forge-alpha', 'forge-beta'],
    );
    expect(getMatchingTargets(targets, ['gpu', 'batch'], 'all')).toEqual([]);
  });

  it('parses, formats, and validates resource values', () => {
    expect(parseResourceValue('500m', 'cores')).toBe(0.5);
    expect(parseResourceValue(' 2 ', 'cores')).toBe(2);
    expect(parseResourceValue('2Gi', 'bytes')).toBe(2 * 1024 ** 3);
    expect(parseResourceValue('512Mi', 'bytes')).toBe(512 * 1024 ** 2);
    expect(parseResourceValue('1Ti', 'bytes')).toBe(1024 ** 4);
    expect(parseResourceValue('', 'cores')).toBeNaN();
    expect(parseResourceValue('3', 'count')).toBe(3);
    expect(Number.isNaN(parseResourceValue('oops', 'bytes'))).toBe(true);
    expect(formatResourceValue(2 * 1024 ** 3, 'bytes')).toBe('2Gi');
    expect(formatResourceValue(1.5, 'cores')).toBe('1.5 cores');
    expect(formatResourceValue(2.5, 'count')).toBe('2.5');
    expect(formatResourceValue(Number.NaN, 'bytes')).toBe('unknown');
  });

  it('aggregates cluster capacity and reports resource errors', () => {
    const clusterResources: ClusterResourceInfo = {
      resourceTypes: [
        { name: 'cpu', resourceKey: 'cpu', displayName: 'CPU', unit: 'cores' },
        { name: 'memory', resourceKey: 'memory', displayName: 'Memory', unit: 'bytes' },
        { name: 'gpu', resourceKey: 'gpu', displayName: 'GPU', unit: 'cores' },
      ],
      nodes: [
        { name: 'node-a', available: { cpu: '2', memory: '8Gi', gpu: '1' } },
        { name: 'node-b', available: { cpu: '500m', memory: '4Gi' } },
        { name: 'node-c', available: { cpu: 'bad', memory: 'oops', gpu: 'nan' } },
      ],
    };

    const totals = aggregateResourceCapacity(clusterResources);
    expect(totals.get('cpu')?.total).toBe(2.5);
    expect(totals.get('memory')?.total).toBe(12 * 1024 ** 3);
    expect(totals.get('gpu')?.total).toBe(1);
    expect(aggregateResourceCapacity(null).size).toBe(0);

    expect(getResourceErrors(makeForm({ cpu: 'oops' }), clusterResources).cpu).toBe(
      'Invalid format',
    );
    expect(getResourceErrors(makeForm({ cpu: '4' }), clusterResources).cpu).toContain(
      'Exceeds available capacity',
    );
    expect(getResourceErrors(makeForm({ mem: '1Gi', gpu: '0' }), clusterResources)).toEqual({});
    expect(
      getResourceErrors(makeForm({ gpu: '2' }), {
        ...clusterResources,
        nodes: [{ name: 'node-a', available: { cpu: '4', memory: '16Gi' } }],
      }),
    ).toEqual({});
  });

  it('slugifies and validates session names', () => {
    expect(slugifySessionName(' Feature / Branch ')).toBe('feature-branch');
    expect(slugifySessionName('UPPER_and spaces')).toBe('upper-and-spaces');
    expect(validateSessionName('')).toBeNull();
    expect(validateSessionName('x'.repeat(64))).toBe('Session name must be 63 characters or fewer');
    expect(validateSessionName('Bad Name')).toBe('Session name must be lowercase');
    expect(validateSessionName('bad name')).toBe('Session name must not contain spaces');
    expect(validateSessionName('-bad')).toBe(
      'Session name must start and end with a letter or digit',
    );
    expect(validateSessionName('bad_underscore')).toBe(
      'Session name may only contain lowercase letters, digits, and hyphens',
    );
    expect(validateSessionName('good-name')).toBeNull();
  });

  it('derives session names from explicit, git, and local mount sources', () => {
    expect(deriveSessionName(makeForm({ sessionName: 'My Session' }))).toBe('my-session');
    expect(deriveSessionName(makeForm({ sessionName: '', branch: 'feat/add-nav' }))).toBe(
      'add-nav',
    );
    expect(
      deriveSessionName(
        makeForm({ sourcetype: 'local_mount', sessionName: '', mountPath: '~/code/niuu/app' }),
      ),
    ).toBe('app');
    expect(
      deriveSessionName(makeForm({ sourcetype: 'local_mount', sessionName: '', mountPath: '~' })),
    ).toBe('home');
    expect(deriveSessionName(makeForm({ sourcetype: 'blank', sessionName: '' }))).toBe(
      'forge-session',
    );
  });

  it('builds session sources and resource configs for each source type', () => {
    expect(buildSessionSource(makeForm())).toEqual({
      type: 'git',
      repo: 'github.com/niuulabs/volundr',
      branch: 'feature/my-work',
    });
    expect(buildSessionSource(makeForm({ sourcetype: 'blank' }))).toEqual({
      type: 'git',
      repo: '',
      branch: '',
    });
    expect(
      buildSessionSource(makeForm({ sourcetype: 'local_mount', mountPath: '~/code/niuu' })),
    ).toEqual({
      type: 'local_mount',
      local_path: '~/code/niuu',
      paths: [{ host_path: '~/code/niuu', mount_path: '/workspace', read_only: false }],
    });
    expect(buildSessionSource(makeForm({ sourcetype: 'local_mount', mountPath: '   ' }))).toEqual({
      type: 'local_mount',
      local_path: '',
      paths: [],
    });

    expect(buildResourceConfig(makeForm({ gpu: '0' }))).toEqual({ cpu: '2', memory: '8Gi' });
    expect(buildResourceConfig(makeForm({ cpu: ' ', mem: ' ', gpu: '0' }))).toBeUndefined();
    expect(buildResourceConfig(makeForm({ cpu: ' ', mem: '1Gi', gpu: '1' }))).toEqual({
      memory: '1Gi',
      gpu: '1',
    });
  });

  it('normalizes environment variables and detects preset-backed runtime features', () => {
    expect(
      normalizeEnvVars([
        { key: ' LOG_LEVEL ', value: 'debug' },
        { key: '', value: 'ignored' },
      ]),
    ).toEqual({ LOG_LEVEL: 'debug' });
    expect(hasPresetBackedRuntime(makeForm())).toBe(false);
    expect(
      hasPresetBackedRuntime(
        makeForm({ mcpServers: [{ name: 'filesystem', type: 'stdio', command: 'uvx' }] }),
      ),
    ).toBe(true);
    expect(hasPresetBackedRuntime(makeForm({ envVars: [{ key: 'A', value: '1' }] }))).toBe(true);
    expect(hasPresetBackedRuntime(makeForm({ setupScripts: ['echo hi'] }))).toBe(true);
    expect(hasPresetBackedRuntime(makeForm({ personaName: 'reviewer' }))).toBe(true);
  });

  it('builds preset payload variants for git, local mount, and blank flows', () => {
    const gitPayload = buildPresetRuntimePayload(
      makeForm({
        selectedCredentials: ['GITHUB_TOKEN'],
        selectedIntegrations: ['int-1'],
        envVars: [{ key: 'LOG_LEVEL', value: 'debug' }],
        setupScripts: ['echo hi', '   '],
      }),
      'launch-preset',
    );
    expect(gitPayload.name).toBe('launch-preset');
    expect(gitPayload.cliTool).toBe('claude');
    expect(gitPayload.source).toEqual({
      type: 'git',
      repo: 'github.com/niuulabs/volundr',
      branch: 'feature/my-work',
    });
    expect(gitPayload.envVars).toEqual({ LOG_LEVEL: 'debug' });
    expect(gitPayload.setupScripts).toEqual(['echo hi']);
    expect(buildPresetRuntimePayload(makeForm({ presetId: 'preset-existing' })).name).toBe(
      'preset-existing',
    );

    const localPayload = buildPresetRuntimePayload(
      makeForm({ sourcetype: 'local_mount', mountPath: '~/code/niuu/local' }),
      'local',
    );
    expect(localPayload.source).toEqual({
      type: 'local_mount',
      local_path: '~/code/niuu/local',
      paths: [{ host_path: '~/code/niuu/local', mount_path: '/workspace', read_only: false }],
    });
    expect(
      buildPresetRuntimePayload(makeForm({ sourcetype: 'local_mount', mountPath: '   ' }), 'local')
        .source,
    ).toBeNull();

    const blankPayload = buildYamlRuntimeFields(makeForm({ sourcetype: 'blank' }));
    expect(blankPayload.source).toBeNull();
    expect(buildPresetRuntimePayload(makeForm({ sourcetype: 'blank' })).source).toBeNull();
    expect(buildYamlRuntimeFields(makeForm()).source).toEqual({
      type: 'git',
      repo: 'github.com/niuulabs/volundr',
      branch: 'feature/my-work',
    });
    expect(
      buildYamlRuntimeFields(makeForm({ sourcetype: 'local_mount', mountPath: '   ' })).source,
    ).toEqual({
      type: 'local_mount',
      local_path: '',
      paths: [],
    });
    expect(buildPresetPayload(makeForm(), 'saved-name').name).toBe('saved-name');
    expect(
      buildPresetPayload(makeForm({ personaName: 'reviewer' }), 'persona-spec').workloadConfig,
    ).toEqual({ persona: 'reviewer' });
  });

  it('copies existing preset state into comparison payloads', () => {
    const preset: VolundrLaunchSpec = {
      id: 'preset-1',
      scope: 'user',
      name: 'Saved preset',
      description: 'desc',
      isDefault: false,
      sessionDefinition: null,
      repos: [],
      workspaceLayout: {},
      cliTool: 'claude',
      workloadType: 'skuld-claude',
      model: 'sonnet-primary',
      systemPrompt: 'system',
      resourceConfig: { cpu: '2' },
      mcpServers: [],
      terminalSidecar: { enabled: true, allowedCommands: [] },
      skills: [],
      rules: [],
      envVars: { LOG_LEVEL: 'debug' },
      envSecretRefs: ['GITHUB_TOKEN'],
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      integrationIds: ['int-1'],
      setupScripts: ['echo hi'],
      workloadConfig: {},
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    expect(buildPresetComparisonPayload(preset)).toMatchObject({
      name: 'Saved preset',
      envVars: { LOG_LEVEL: 'debug' },
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
    });
  });
});
