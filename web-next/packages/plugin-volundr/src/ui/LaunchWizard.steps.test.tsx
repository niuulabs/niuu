import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type {
  ClusterResourceInfo,
  IntegrationConnection,
  SessionDefinition,
  StoredCredential,
  VolundrModel,
  VolundrPreset,
  VolundrWorkspace,
} from '../models/volundr.model';
import type { Template } from '../domain/template';
import type { RepoRecord } from '@niuulabs/ui';
import {
  BootingStep,
  ConfirmStep,
  RuntimeStep,
  SourceStep,
  StepIndicator,
  TemplateStep,
  type WizardForm,
} from './LaunchWizard';

const TEMPLATE: Template = {
  id: 'tpl-platform',
  name: 'Platform Forge',
  description: 'Launch the platform workspace',
  source: { kind: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
  resources: { cpu: '2', memory: '8Gi' },
  prompts: { system: '', initial: '' },
  metadata: {},
  spec: {
    image: 'ghcr.io/niuulabs/volundr',
    tag: 'latest',
    resources: { cpuRequest: 2, memRequestMi: 8192, gpuCount: 0 },
  },
  usageCount: 7,
};

const MODELS: Record<string, VolundrModel> = {
  'sonnet-primary': { name: 'Sonnet', provider: 'anthropic', tier: 'smart' },
  'gpt-test': { name: 'GPT Test', provider: 'openai', tier: 'fast' },
};

const CREDENTIALS: StoredCredential[] = [
  { name: 'GITHUB_TOKEN', provider: 'github', updatedAt: new Date().toISOString() },
];

const INTEGRATIONS: IntegrationConnection[] = [
  {
    id: 'int-1',
    slug: 'github-app',
    credentialName: 'prod-github',
    integrationType: 'source_control',
    adapter: 'github',
    status: 'connected',
  },
];

const WORKSPACES: VolundrWorkspace[] = [
  {
    id: 'ws-1',
    pvcName: 'workspace-pvc',
    sessionId: null,
    status: 'available',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    sessionName: 'reuse-me',
    sourceUrl: 'https://github.com/niuulabs/volundr.git',
    sourceRef: 'main',
    lastUsedAt: null,
    deletedAt: null,
  },
];

const PRESET: VolundrPreset = {
  id: 'preset-1',
  name: 'Saved preset',
  description: 'preset description',
  isDefault: false,
  cliTool: 'claude',
  workloadType: 'skuld-claude',
  model: 'sonnet-primary',
  systemPrompt: '',
  resourceConfig: { cpu: '2', memory: '8Gi' },
  mcpServers: [],
  terminalSidecar: { enabled: true, allowedCommands: [] },
  skills: [],
  rules: [],
  envVars: {},
  envSecretRefs: [],
  source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
  integrationIds: [],
  setupScripts: [],
  workloadConfig: {},
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

const DEFINITIONS: SessionDefinition[] = [
  {
    key: 'skuld-claude',
    displayName: 'Claude Code',
    description: '',
    labels: [],
    defaultModel: 'sonnet-primary',
  },
  {
    key: 'skuld-codex',
    displayName: 'Codex',
    description: '',
    labels: [],
    defaultModel: 'gpt-test',
  },
];

const REPOS: RepoRecord[] = [
  {
    id: 'repo-1',
    name: 'volundr',
    owner: 'niuulabs',
    provider: 'github',
    cloneUrl: 'github.com/niuulabs/volundr',
    defaultBranch: 'main',
    branches: ['main', 'feat/coverage'],
  },
];

const CLUSTER_RESOURCES: ClusterResourceInfo = {
  resourceTypes: [
    { name: 'cpu', resourceKey: 'cpu', displayName: 'CPU', unit: 'cores' },
    { name: 'memory', resourceKey: 'memory', displayName: 'Memory', unit: 'bytes' },
    { name: 'gpu', resourceKey: 'gpu', displayName: 'GPU', unit: 'count' },
  ],
  nodes: [{ name: 'node-a', available: { cpu: '4', memory: '16Gi', gpu: '1' } }],
};

function makeForm(overrides: Partial<WizardForm> = {}): WizardForm {
  return {
    templateId: TEMPLATE.id,
    presetId: '',
    sourcetype: 'git',
    repo: 'github.com/niuulabs/volundr',
    branch: 'main',
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

describe('LaunchWizard step components', () => {
  it('renders indicator, template cards, confirm summary, and boot progress', () => {
    const onSelect = vi.fn();
    render(
      <div>
        <StepIndicator current="runtime" steps={['template', 'source', 'runtime', 'confirm']} />
        <TemplateStep templates={[TEMPLATE]} selectedId="" onSelect={onSelect} />
        <ConfirmStep
          form={makeForm({
            selectedCredentials: ['GITHUB_TOKEN'],
            selectedIntegrations: ['int-1'],
            envVars: [{ key: 'LOG_LEVEL', value: 'debug' }],
            setupScripts: ['pnpm install'],
            initialPrompt: 'Ship it',
          })}
          templates={[TEMPLATE]}
          models={MODELS}
          integrations={INTEGRATIONS}
          sessionDefinitions={DEFINITIONS}
        />
        <BootingStep bootStep={2} progress={0.5} />
      </div>,
    );

    fireEvent.click(screen.getByTestId('wizard-template-card'));
    expect(onSelect).toHaveBeenCalledWith(TEMPLATE.id);
    expect(screen.getAllByText('Platform Forge').length).toBeGreaterThan(0);
    expect(screen.getByText('Launch summary')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getAllByTestId('boot-step')).toHaveLength(8);
  });

  it('handles source changes, tracker selection, and linked issue clearing', () => {
    const update = vi.fn();
    const trackerIssue = {
      id: 'issue-1',
      identifier: 'NIU-801',
      title: 'Hook tracker issue launch context into sessions',
      status: 'todo',
      projectId: null,
      projectName: null,
      url: null,
    };

    const { rerender } = render(
      <SourceStep
        form={makeForm()}
        update={update}
        repos={REPOS}
        branchOptions={['main', 'feat/coverage']}
        trackerResults={[trackerIssue]}
        trackerLoading={false}
      />,
    );

    fireEvent.click(screen.getByTestId('source-tab-local_mount'));
    fireEvent.click(screen.getByTestId('source-tab-blank'));
    fireEvent.click(screen.getByText('NIU-801'));

    expect(update).toHaveBeenCalledWith({ sourcetype: 'local_mount' });
    expect(update).toHaveBeenCalledWith({ sourcetype: 'blank' });
    expect(update).toHaveBeenCalledWith({
      trackerIssue,
      trackerQuery: 'NIU-801',
    });

    rerender(
      <SourceStep
        form={makeForm({ trackerIssue, trackerQuery: 'NIU-801' })}
        update={update}
        repos={REPOS}
        branchOptions={['main']}
        trackerResults={[]}
        trackerLoading={false}
      />,
    );

    fireEvent.click(screen.getByText('clear'));
    expect(update).toHaveBeenCalledWith({ trackerIssue: null, trackerQuery: '' });
  });

  it('updates source form fields for repo, branch, mount path, and session naming', () => {
    const update = vi.fn();

    const { rerender } = render(
      <SourceStep
        form={makeForm()}
        update={update}
        repos={REPOS}
        branchOptions={['main', 'feat/coverage']}
        trackerResults={[]}
        trackerLoading={false}
      />,
    );

    fireEvent.change(screen.getByTestId('repo-select'), {
      target: { value: 'github.com/niuulabs/volundr' },
    });
    fireEvent.change(screen.getByTestId('branch-select'), {
      target: { value: 'feat/coverage' },
    });
    fireEvent.change(screen.getByPlaceholderText('auto-generated from branch if blank'), {
      target: { value: 'coverage-session' },
    });
    fireEvent.change(screen.getByPlaceholderText('Search tracker issues'), {
      target: { value: 'NIU-900' },
    });

    expect(update).toHaveBeenCalledWith({
      repo: 'github.com/niuulabs/volundr',
      branch: 'main',
      workspaceId: '',
    });
    expect(update).toHaveBeenCalledWith({ branch: 'feat/coverage' });
    expect(update).toHaveBeenCalledWith({ sessionName: 'coverage-session' });
    expect(update).toHaveBeenCalledWith({ trackerQuery: 'NIU-900' });

    rerender(
      <SourceStep
        form={makeForm({ sourcetype: 'local_mount' })}
        update={update}
        repos={REPOS}
        branchOptions={[]}
        trackerResults={[]}
        trackerLoading
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('~/code/niuu'), {
      target: { value: '~/code/niuu/volundr' },
    });
    expect(update).toHaveBeenCalledWith({ mountPath: '~/code/niuu/volundr' });
    expect(screen.getByText('searching…')).toBeInTheDocument();
  });

  it('handles runtime preset saving, yaml mode, and custom MCP configuration', async () => {
    const update = vi.fn();
    const onApplyPreset = vi.fn();
    const onSavePreset = vi.fn(async () => {});

    render(
      <RuntimeStep
        form={makeForm()}
        update={update}
        models={MODELS}
        workspaces={WORKSPACES}
        credentials={CREDENTIALS}
        integrations={INTEGRATIONS}
        clusterResources={CLUSTER_RESOURCES}
        presets={[PRESET]}
        selectedPreset={PRESET}
        availableMcpServers={[
          { name: 'filesystem', type: 'stdio', command: 'uvx', args: ['mcp-filesystem'] },
        ]}
        sessionDefinitions={DEFINITIONS}
        onApplyPreset={onApplyPreset}
        onSavePreset={onSavePreset}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('save as preset'), {
      target: { value: 'coverage-preset' },
    });
    fireEvent.click(screen.getByText('save'));
    await waitFor(() => expect(onSavePreset).toHaveBeenCalledWith('coverage-preset'));

    fireEvent.change(screen.getByDisplayValue('Custom (no preset)'), {
      target: { value: PRESET.id },
    });
    expect(onApplyPreset).toHaveBeenCalledWith(PRESET.id);

    fireEvent.click(screen.getByTestId('cli-option-codex'));
    expect(update).toHaveBeenCalledWith({ definition: 'skuld-codex', model: 'gpt-test' });

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('add env var'));
    fireEvent.click(screen.getByText('add script'));
    expect(update).toHaveBeenCalledWith({ envVars: [{ key: '', value: '' }] });
    expect(update).toHaveBeenCalledWith({ setupScripts: [''] });

    fireEvent.click(screen.getByText('add custom server'));
    fireEvent.change(screen.getByPlaceholderText('filesystem'), {
      target: { value: 'local-files' },
    });
    fireEvent.change(screen.getByPlaceholderText('uvx'), { target: { value: 'npx' } });
    fireEvent.change(screen.getByPlaceholderText('mcp-filesystem /workspace'), {
      target: { value: 'mcp-filesystem /workspace' },
    });
    fireEvent.change(screen.getByPlaceholderText('KEY'), { target: { value: 'API_KEY' } });
    fireEvent.change(screen.getByPlaceholderText('value'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByText('add'));
    fireEvent.click(screen.getByText('add server'));

    expect(update).toHaveBeenCalledWith({
      mcpServers: [
        {
          name: 'local-files',
          type: 'stdio',
          command: 'npx',
          args: ['mcp-filesystem', '/workspace'],
          env: { API_KEY: 'secret' },
        },
      ],
    });

    fireEvent.click(screen.getByText('edit as yaml'));
    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({
        yamlMode: true,
        yamlContent: expect.stringContaining('cli_tool: claude'),
      }),
    );
  });

  it('handles runtime field updates, credential toggles, and removal flows', () => {
    const update = vi.fn();

    render(
      <RuntimeStep
        form={makeForm({
          workspaceId: 'ws-1',
          selectedCredentials: ['GITHUB_TOKEN'],
          selectedIntegrations: ['int-1'],
          mcpServers: [{ name: 'existing-mcp', type: 'stdio', command: 'uvx', args: ['server'] }],
          envVars: [{ key: 'LOG_LEVEL', value: 'debug' }],
          setupScripts: ['pnpm install'],
        })}
        update={update}
        models={MODELS}
        workspaces={WORKSPACES}
        credentials={CREDENTIALS}
        integrations={INTEGRATIONS}
        clusterResources={CLUSTER_RESOURCES}
        presets={[PRESET]}
        selectedPreset={null}
        availableMcpServers={[
          { name: 'filesystem', type: 'stdio', command: 'uvx', args: ['mcp-filesystem'] },
        ]}
        sessionDefinitions={DEFINITIONS}
        onApplyPreset={vi.fn()}
        onSavePreset={vi.fn(async () => {})}
      />,
    );

    fireEvent.change(screen.getByTestId('model-select'), { target: { value: 'gpt-test' } });
    fireEvent.change(screen.getByTestId('permission-select'), { target: { value: 'normal' } });
    fireEvent.change(screen.getByTestId('workspace-select'), { target: { value: '__new__' } });
    fireEvent.change(screen.getByPlaceholderText('2'), { target: { value: '3' } });
    fireEvent.change(screen.getByPlaceholderText('8Gi'), { target: { value: '12Gi' } });
    fireEvent.change(screen.getByPlaceholderText('0'), { target: { value: '1' } });

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('filesystem'));
    fireEvent.click(screen.getAllByText('remove')[0]!);
    fireEvent.change(screen.getByDisplayValue('LOG_LEVEL'), { target: { value: 'API_KEY' } });
    fireEvent.change(screen.getByDisplayValue('debug'), { target: { value: 'secret' } });
    fireEvent.click(screen.getAllByText('remove')[1]!);
    fireEvent.change(screen.getByDisplayValue('pnpm install'), { target: { value: 'pnpm test' } });
    fireEvent.click(screen.getAllByText('remove')[2]!);
    fireEvent.click(screen.getByLabelText('GITHUB_TOKEN'));
    fireEvent.click(screen.getByText('Github App · prod-github'));

    expect(update).toHaveBeenCalledWith({ model: 'gpt-test' });
    expect(update).toHaveBeenCalledWith({ permission: 'normal' });
    expect(update).toHaveBeenCalledWith({ workspaceId: '' });
    expect(update).toHaveBeenCalledWith({ cpu: '3' });
    expect(update).toHaveBeenCalledWith({ mem: '12Gi' });
    expect(update).toHaveBeenCalledWith({ gpu: '1' });
    expect(update).toHaveBeenCalledWith({
      mcpServers: [
        { name: 'existing-mcp', type: 'stdio', command: 'uvx', args: ['server'] },
        { name: 'filesystem', type: 'stdio', command: 'uvx', args: ['mcp-filesystem'] },
      ],
    });
    expect(update).toHaveBeenCalledWith({ mcpServers: [] });
    expect(update).toHaveBeenCalledWith({ envVars: [{ key: 'API_KEY', value: 'debug' }] });
    expect(update).toHaveBeenCalledWith({ envVars: [{ key: 'LOG_LEVEL', value: 'secret' }] });
    expect(update).toHaveBeenCalledWith({ envVars: [] });
    expect(update).toHaveBeenCalledWith({ setupScripts: ['pnpm test'] });
    expect(update).toHaveBeenCalledWith({ setupScripts: [] });
    expect(update).toHaveBeenCalledWith({ selectedCredentials: [] });
    expect(update).toHaveBeenCalledWith({ selectedIntegrations: [] });
  });

  it('parses yaml settings back into form patches when switching to form view', async () => {
    const update = vi.fn();
    render(
      <RuntimeStep
        form={makeForm({
          yamlMode: true,
          yamlContent: [
            'cli_tool: codex',
            'model: gpt-test',
            'resource_config:',
            '  cpu: "3"',
            '  memory: "12Gi"',
            'source:',
            '  type: git',
            '  repo: github.com/niuulabs/volundr',
            '  branch: feat/yaml',
          ].join('\n'),
        })}
        update={update}
        models={MODELS}
        workspaces={WORKSPACES}
        credentials={CREDENTIALS}
        integrations={INTEGRATIONS}
        clusterResources={CLUSTER_RESOURCES}
        presets={[]}
        selectedPreset={null}
        availableMcpServers={[]}
        sessionDefinitions={DEFINITIONS}
        onApplyPreset={vi.fn()}
        onSavePreset={vi.fn(async () => {})}
      />,
    );

    fireEvent.click(screen.getByText('show advanced'));
    fireEvent.click(screen.getByText('form view'));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        expect.objectContaining({
          yamlMode: false,
          definition: 'skuld-codex',
          model: 'gpt-test',
          cpu: '3',
          mem: '12Gi',
          sourcetype: 'git',
          repo: 'github.com/niuulabs/volundr',
          branch: 'feat/yaml',
        }),
      ),
    );
  });
});
