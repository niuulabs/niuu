import { describe, expect, it } from 'vitest';
import type {
  ClusterResourceInfo,
  IntegrationConnection,
  VolundrModel,
  VolundrPreset,
  VolundrWorkspace,
} from '../models/volundr.model';
import type { Template } from '../domain/template';
import {
  aggregateResourceCapacity,
  buildPresetComparisonPayload,
  buildPresetPayload,
  buildPresetRuntimePayload,
  buildResourceConfig,
  buildSessionSource,
  buildYamlRuntimeFields,
  deriveCliTool,
  deriveSessionName,
  formatIntegrationLabel,
  formatIntegrationMeta,
  formatModelOption,
  formatResourceValue,
  getDefinitionRune,
  getResourceErrors,
  hasPresetBackedRuntime,
  normalizeEnvVars,
  normalizeRepoUrl,
  parseResourceValue,
  pickDefaultModel,
  slugifySessionName,
  validateSessionName,
  workspaceLabel,
  type WizardForm,
} from './LaunchWizard';

function makeForm(overrides: Partial<WizardForm> = {}): WizardForm {
  return {
    templateId: 'tpl-default',
    presetId: '',
    sourcetype: 'git',
    repo: 'github.com/niuulabs/volundr',
    branch: 'feature/my-work',
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
    definition: 'skuld-claude',
    model: 'sonnet-primary',
    permission: 'restricted',
    cpu: '2',
    mem: '8Gi',
    gpu: '0',
    cluster: '',
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
    expect(deriveCliTool('codex')).toBe('codex');
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
    expect(formatModelOption('gpt-test', models['gpt-test'])).toBe('GPT Test · openai · fast');
    expect(formatModelOption('fallback')).toBe('fallback');

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
    expect(formatIntegrationMeta({ ...integration, integrationType: null })).toBe('prod-github');
  });

  it('parses, formats, and validates resource values', () => {
    expect(parseResourceValue('500m', 'cores')).toBe(0.5);
    expect(parseResourceValue('2Gi', 'bytes')).toBe(2 * 1024 ** 3);
    expect(Number.isNaN(parseResourceValue('oops', 'bytes'))).toBe(true);
    expect(formatResourceValue(2 * 1024 ** 3, 'bytes')).toBe('2Gi');
    expect(formatResourceValue(1.5, 'cores')).toBe('1.5 cores');
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
  });

  it('slugifies and validates session names', () => {
    expect(slugifySessionName(' Feature / Branch ')).toBe('feature-branch');
    expect(slugifySessionName('UPPER_and spaces')).toBe('upper-and-spaces');
    expect(validateSessionName('')).toBeNull();
    expect(validateSessionName('Bad Name')).toBe('Session name must be lowercase');
    expect(validateSessionName('bad name')).toBe('Session name must not contain spaces');
    expect(validateSessionName('-bad')).toBe(
      'Session name must start and end with a letter or digit',
    );
    expect(validateSessionName('good-name')).toBeNull();
  });

  it('derives session names from explicit, git, local mount, and template sources', () => {
    const template: Template = {
      id: 'tpl-default',
      name: 'Release Train',
      description: '',
      source: { kind: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      resources: { cpu: '2', memory: '8Gi' },
      prompts: { system: '', initial: '' },
      metadata: {},
    };

    expect(deriveSessionName(makeForm({ sessionName: 'My Session' }), template)).toBe('my-session');
    expect(deriveSessionName(makeForm({ sessionName: '', branch: 'feat/add-nav' }), template)).toBe(
      'add-nav',
    );
    expect(
      deriveSessionName(
        makeForm({ sourcetype: 'local_mount', sessionName: '', mountPath: '~/code/niuu/app' }),
        template,
      ),
    ).toBe('app');
    expect(deriveSessionName(makeForm({ sourcetype: 'blank', sessionName: '' }), template)).toBe(
      'release-train',
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

    expect(buildResourceConfig(makeForm({ gpu: '0' }))).toEqual({ cpu: '2', memory: '8Gi' });
    expect(buildResourceConfig(makeForm({ cpu: ' ', mem: ' ', gpu: '0' }))).toBeUndefined();
  });

  it('normalizes environment variables and detects preset-backed runtime features', () => {
    expect(
      normalizeEnvVars([
        { key: ' LOG_LEVEL ', value: 'debug' },
        { key: '', value: 'ignored' },
      ]),
    ).toEqual({ LOG_LEVEL: 'debug' });
    expect(hasPresetBackedRuntime(makeForm())).toBe(false);
    expect(hasPresetBackedRuntime(makeForm({ envVars: [{ key: 'A', value: '1' }] }))).toBe(true);
    expect(hasPresetBackedRuntime(makeForm({ setupScripts: ['echo hi'] }))).toBe(true);
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

    const localPayload = buildPresetRuntimePayload(
      makeForm({ sourcetype: 'local_mount', mountPath: '~/code/niuu/local' }),
      'local',
    );
    expect(localPayload.source).toEqual({
      type: 'local_mount',
      local_path: '~/code/niuu/local',
      paths: [{ host_path: '~/code/niuu/local', mount_path: '/workspace', read_only: false }],
    });

    const blankPayload = buildYamlRuntimeFields(makeForm({ sourcetype: 'blank' }));
    expect(blankPayload.source).toBeNull();
    expect(buildPresetPayload(makeForm(), 'saved-name').name).toBe('saved-name');
  });

  it('copies existing preset state into comparison payloads', () => {
    const preset: VolundrPreset = {
      id: 'preset-1',
      name: 'Saved preset',
      description: 'desc',
      isDefault: false,
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
