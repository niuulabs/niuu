import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { LaunchCatalogPage } from './LaunchCatalogPage';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { createMockVolundrService } from '../adapters/mock';
import type { VolundrLaunchSpec } from '../models/volundr.model';

function makeSpec(
  overrides: Partial<VolundrLaunchSpec> & Pick<VolundrLaunchSpec, 'name'>,
): VolundrLaunchSpec {
  return {
    name: overrides.name,
    scope: overrides.scope ?? 'user',
    id: 'id' in overrides ? (overrides.id ?? null) : `spec-${overrides.name}`,
    description: overrides.description ?? '',
    isDefault: overrides.isDefault ?? false,
    sessionDefinition: overrides.sessionDefinition ?? null,
    workloadType: overrides.workloadType ?? 'default',
    model: overrides.model ?? null,
    systemPrompt: overrides.systemPrompt ?? null,
    resourceConfig: overrides.resourceConfig ?? {},
    mcpServers: overrides.mcpServers ?? [],
    envVars: overrides.envVars ?? {},
    envSecretRefs: overrides.envSecretRefs ?? [],
    workloadConfig: overrides.workloadConfig ?? {},
    repos: overrides.repos ?? [],
    source: overrides.source ?? null,
    setupScripts: overrides.setupScripts ?? [],
    workspaceLayout: overrides.workspaceLayout ?? {},
    cliTool: overrides.cliTool ?? 'claude',
    terminalSidecar: overrides.terminalSidecar ?? { enabled: false, allowedCommands: [] },
    skills: overrides.skills ?? [],
    rules: overrides.rules ?? [],
    integrationIds: overrides.integrationIds ?? [],
    createdAt: overrides.createdAt ?? '2026-01-01T00:00:00Z',
    updatedAt: overrides.updatedAt ?? '2026-01-01T00:00:00Z',
  };
}

function renderWithSpecs(specs: VolundrLaunchSpec[]) {
  return renderWithVolundr(<LaunchCatalogPage />, {
    service: {
      ...createMockVolundrService(),
      getLaunchSpecs: async () => specs,
    },
  });
}

async function waitForPage() {
  await waitFor(() => expect(screen.getByTestId('launch-catalog-page')).toBeInTheDocument());
}

describe('LaunchCatalogPage', () => {
  it('renders loading state while specs resolve', () => {
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: () => new Promise<never>(() => {}),
      },
    });

    expect(screen.getByText(/loading catalog/i)).toBeInTheDocument();
  });

  it('renders error state with the Error message', async () => {
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => {
          throw new Error('catalog unavailable');
        },
      },
    });

    await waitFor(() => expect(screen.getByText(/failed to load catalog/i)).toBeInTheDocument());
    expect(screen.getByText('catalog unavailable')).toBeInTheDocument();
  });

  it('renders a generic error message for non-Error rejections', async () => {
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: () => Promise.reject('string failure'),
      },
    });

    await waitFor(() => expect(screen.getByText(/failed to load catalog/i)).toBeInTheDocument());
    expect(screen.getByText('Unknown error')).toBeInTheDocument();
  });

  it('renders the empty state when no specs are configured', async () => {
    renderWithSpecs([]);

    await waitForPage();
    expect(screen.getByText(/no catalog specs configured yet/i)).toBeInTheDocument();
  });

  it('renders the catalog sidebar and one list item per spec', async () => {
    renderWithSpecs([makeSpec({ name: 'alpha-spec' }), makeSpec({ name: 'beta-spec' })]);

    await waitForPage();
    expect(screen.getByRole('heading', { name: 'Catalog' })).toBeInTheDocument();
    expect(screen.getByText('workspace + runtime bundles')).toBeInTheDocument();
    expect(screen.getAllByText('alpha-spec').length).toBeGreaterThan(0);
    expect(screen.getByText('beta-spec')).toBeInTheDocument();
    expect(screen.getAllByTestId('catalog-template-item')).toHaveLength(2);
    expect(screen.queryByText(/no catalog specs configured yet/i)).not.toBeInTheDocument();
  });

  it('filters catalog specs and shows the empty selection state', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'alpha-spec',
        mcpServers: [{ name: 'mimir', type: 'stdio' }],
      }),
      makeSpec({ name: 'beta-spec' }),
    ]);

    await waitForPage();
    fireEvent.change(screen.getByPlaceholderText('Filter catalog...'), {
      target: { value: 'mimir' },
    });
    expect(screen.getAllByTestId('catalog-template-item')).toHaveLength(1);
    expect(screen.getAllByText('alpha-spec').length).toBeGreaterThan(0);

    fireEvent.change(screen.getByPlaceholderText('Filter catalog...'), {
      target: { value: 'missing' },
    });
    expect(screen.queryAllByTestId('catalog-template-item')).toHaveLength(0);
    expect(screen.getAllByText('alpha-spec').length).toBeGreaterThan(0);
  });

  it('collapses and expands the catalog sidebar', async () => {
    renderWithSpecs([makeSpec({ name: 'alpha-spec' })]);

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /collapse catalog sidebar/i }));
    expect(screen.getByText('catalog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /expand catalog sidebar/i }));
    expect(screen.getByText('workspace + runtime bundles')).toBeInTheDocument();
  });

  it('renders specs from the default mock service', async () => {
    renderWithVolundr(<LaunchCatalogPage />);

    await waitForPage();
    expect(screen.getAllByTestId('catalog-template-item').length).toBeGreaterThan(0);
  });

  it('shows the default badge only for default specs', async () => {
    renderWithSpecs([
      makeSpec({ name: 'default-spec', isDefault: true, workloadType: 'worker' }),
      makeSpec({ name: 'plain-spec', isDefault: false, workloadType: 'worker' }),
    ]);

    await waitForPage();
    expect(screen.getAllByText('default').length).toBeGreaterThan(0);
    const plainRow = screen.getByText('plain-spec').closest('button');
    expect(plainRow).not.toBeNull();
    expect(within(plainRow!).queryByText('default')).not.toBeInTheDocument();
  });

  it('falls back to placeholder copy when description is empty', async () => {
    renderWithSpecs([
      makeSpec({ name: 'described', description: 'Runs the nightly batch.' }),
      makeSpec({ name: 'undescribed', description: '' }),
    ]);

    await waitForPage();
    expect(screen.getByText('Runs the nightly batch.')).toBeInTheDocument();
    fireEvent.click(screen.getByText('undescribed'));
    expect(screen.getByText('No description configured.')).toBeInTheDocument();
  });

  it('groups system and user specs in the sidebar', async () => {
    renderWithSpecs([
      makeSpec({ name: 'sys-spec', scope: 'system', id: null }),
      makeSpec({ name: 'user-spec', scope: 'user' }),
    ]);

    await waitForPage();
    expect(screen.getByText('built-in')).toBeInTheDocument();
    expect(screen.getByText('saved')).toBeInTheDocument();
  });

  it('prefers sessionDefinition over workloadType for the runtime field', async () => {
    renderWithSpecs([
      makeSpec({ name: 'with-def', sessionDefinition: 'skuldClaude', workloadType: 'default' }),
      makeSpec({ name: 'without-def', sessionDefinition: null, workloadType: 'batch-runner' }),
    ]);

    await waitForPage();
    expect(screen.getByText('skuldClaude')).toBeInTheDocument();
    fireEvent.click(screen.getByText('without-def'));
    expect(screen.getByText('batch-runner')).toBeInTheDocument();
  });

  it('renders the model or a launch-time placeholder', async () => {
    renderWithSpecs([
      makeSpec({ name: 'with-model', model: 'claude-sonnet' }),
      makeSpec({ name: 'without-model', model: null }),
    ]);

    await waitForPage();
    expect(screen.getByText('claude-sonnet')).toBeInTheDocument();
    fireEvent.click(screen.getByText('without-model'));
    expect(screen.getByText('selected at launch')).toBeInTheDocument();
  });

  it('formats full resource config with cpu, memory and gpu', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'beefy',
        resourceConfig: { cpu: '4', memory: '8Gi', gpu: '1' },
      }),
    ]);

    await waitForPage();
    expect(screen.getByText('4 cores')).toBeInTheDocument();
    expect(screen.getByText('8Gi')).toBeInTheDocument();
    expect(screen.getAllByText('1').length).toBeGreaterThan(0);
  });

  it('omits gpu when it is "0" and falls back when nothing is set', async () => {
    renderWithSpecs([
      makeSpec({ name: 'zero-gpu', resourceConfig: { cpu: '2', memory: '4Gi', gpu: '0' } }),
      makeSpec({ name: 'no-resources', resourceConfig: {} }),
    ]);

    await waitForPage();
    expect(screen.getByText('2 cores')).toBeInTheDocument();
    expect(screen.getByText('4Gi')).toBeInTheDocument();
    fireEvent.click(screen.getByText('no-resources'));
    expect(screen.getAllByText('default').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getAllByText('default').length).toBeGreaterThan(0);
  });

  it('formats partial resource config without cpu', async () => {
    renderWithSpecs([makeSpec({ name: 'mem-only', resourceConfig: { memory: '2Gi' } })]);

    await waitForPage();
    expect(screen.getByText('2Gi')).toBeInTheDocument();
  });

  it('shows launch-time placeholder when no source is configured', async () => {
    renderWithSpecs([makeSpec({ name: 'no-source', source: null })]);

    await waitForPage();
    expect(screen.getByText('source selected at launch')).toBeInTheDocument();
  });

  it('formats git sources as repo @ branch', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'git-source',
        source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      }),
    ]);

    await waitForPage();
    fireEvent.click(screen.getByRole('tab', { name: /workspace/i }));
    expect(screen.getByText('github.com/niuulabs/volundr @ main')).toBeInTheDocument();
  });

  it('formats git sources without a branch', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'git-source',
        source: { type: 'git', repo: 'github.com/niuulabs/volundr' },
      }),
    ]);

    await waitForPage();
    expect(screen.getByText('github.com/niuulabs/volundr')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /workspace/i }));
    expect(screen.getByText('github.com/niuulabs/volundr')).toBeInTheDocument();
  });

  it('formats local mounts using local_path when present', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'local-path',
        source: { type: 'local_mount', local_path: '/home/dev/proj', paths: [] },
      }),
    ]);

    await waitForPage();
    expect(screen.getByText('/home/dev/proj')).toBeInTheDocument();
  });

  it('falls back to the first mount host_path for local mounts', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'host-path',
        source: {
          type: 'local_mount',
          paths: [{ host_path: '/mnt/host/code', mount_path: '/workspace', read_only: false }],
        },
      }),
    ]);

    await waitForPage();
    expect(screen.getByText('/mnt/host/code')).toBeInTheDocument();
  });

  it('labels pathless local mounts as "local mount"', async () => {
    renderWithSpecs([makeSpec({ name: 'bare-mount', source: { type: 'local_mount', paths: [] } })]);

    await waitForPage();
    expect(screen.getByText('local mount')).toBeInTheDocument();
  });

  it('lists configured MCP servers and hides the row when empty', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'with-mcp',
        mcpServers: [
          { name: 'mimir', type: 'stdio' },
          { name: 'bifrost', type: 'stdio' },
        ],
      }),
      makeSpec({ name: 'without-mcp', mcpServers: [] }),
    ]);

    await waitForPage();
    fireEvent.click(screen.getByRole('tab', { name: /mcp/i }));
    expect(screen.getByText('mimir')).toBeInTheDocument();
    expect(screen.getByText('bifrost')).toBeInTheDocument();
    fireEvent.click(screen.getByText('without-mcp'));
    fireEvent.click(screen.getByRole('tab', { name: /mcp/i }));
    expect(screen.getByText('No MCP servers configured.')).toBeInTheDocument();
  });

  it('shows populated workspace, runtime and rules tab details', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'detailed',
        sessionDefinition: null,
        workloadType: 'worker',
        model: null,
        systemPrompt: 'Do the thing.',
        resourceConfig: { cpu: '2', memory: '4Gi', gpu: '1' },
        setupScripts: ['pnpm install'],
        workspaceLayout: { root: '/workspace' },
        skills: [{ name: 'review' }],
        rules: [{ inline: 'Keep it short.' }],
      }),
    ]);

    await waitForPage();
    fireEvent.click(screen.getByRole('tab', { name: /workspace/i }));
    expect(screen.getByText('1 scripts')).toBeInTheDocument();
    expect(screen.getByText('configured')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /runtime/i }));
    expect(screen.getByText('worker')).toBeInTheDocument();
    expect(screen.getByText('2 cores · 4Gi · 1 gpu')).toBeInTheDocument();
    expect(screen.getByText('configured')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: /rules/i }));
    expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(2);
  });

  it('opens the launch wizard from a catalog spec', async () => {
    renderWithSpecs([makeSpec({ name: 'launchable' })]);

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /launch from this/i }));
    await waitFor(() => expect(screen.getByText('Launch pod')).toBeInTheDocument());
  });

  it('shows clone, launch, edit and new actions where appropriate', async () => {
    renderWithSpecs([makeSpec({ name: 'user-template', scope: 'user', id: 'user-template' })]);

    await waitForPage();
    expect(screen.getByRole('button', { name: /clone/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /launch from this/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /new/i })).toBeInTheDocument();
  });

  it('opens the create catalog spec editor', async () => {
    renderWithSpecs([makeSpec({ name: 'starter' })]);

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /new/i }));
    expect(screen.getByText('Create catalog spec')).toBeInTheDocument();
    expect(screen.getByDisplayValue('new-template')).toBeInTheDocument();
    expect(screen.getByDisplayValue('skuldClaude')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('one command per line')).toBeInTheDocument();
  });

  it('lets built-in specs open the edit form as a user copy', async () => {
    renderWithSpecs([makeSpec({ name: 'built-in', scope: 'system', id: null })]);

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    expect(screen.getByText('Edit catalog spec')).toBeInTheDocument();
    expect(
      screen.getByText('Built-in templates are saved as user launch specs.'),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue('built-in-custom')).toBeInTheDocument();
  });

  it('saves a built-in edit as a new user spec', async () => {
    const saveLaunchSpec = vi.fn(async (spec) =>
      makeSpec({ ...spec, id: spec.id ?? 'saved-built-in', scope: 'user', name: spec.name }),
    );
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => [
          makeSpec({
            name: 'built-in',
            scope: 'system',
            id: null,
            description: 'Built-in template',
            sessionDefinition: 'skuldClaude',
            resourceConfig: { cpu: '2', memory: '6Gi', gpu: '0' },
            mcpServers: [{ name: 'filesystem', type: 'stdio' }],
            setupScripts: ['pnpm install'],
          }),
        ],
        saveLaunchSpec,
      },
    });

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(await screen.findByDisplayValue('built-in-custom'), {
      target: { value: 'custom-built-in' },
    });
    fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '' } });
    fireEvent.change(screen.getByDisplayValue('filesystem'), {
      target: { value: 'filesystem, git' },
    });
    fireEvent.change(screen.getByDisplayValue('pnpm install'), {
      target: { value: 'pnpm install\npnpm build' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(saveLaunchSpec).toHaveBeenCalledTimes(1));
    expect(saveLaunchSpec).toHaveBeenCalledWith(
      expect.objectContaining({
        id: undefined,
        name: 'custom-built-in',
        sessionDefinition: 'skuldClaude',
        resourceConfig: { memory: '6Gi', gpu: '0' },
        mcpServers: [
          { name: 'filesystem', type: 'stdio' },
          { name: 'git', type: 'stdio' },
        ],
        setupScripts: ['pnpm install', 'pnpm build'],
      }),
    );
  });

  it('saves an existing user spec in place', async () => {
    const saveLaunchSpec = vi.fn(async (spec) =>
      makeSpec({ ...spec, id: spec.id ?? 'user-template', scope: 'user', name: spec.name }),
    );
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => [
          makeSpec({
            name: 'user-template',
            scope: 'user',
            id: 'user-template',
            sessionDefinition: 'skuldCodex',
            model: 'gpt-5',
          }),
        ],
        saveLaunchSpec,
      },
    });

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gpt-5.1' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(saveLaunchSpec).toHaveBeenCalledTimes(1));
    expect(saveLaunchSpec).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'user-template',
        name: 'user-template',
        model: 'gpt-5.1',
      }),
    );
  });

  it('clones a template as a new user spec', async () => {
    const saveLaunchSpec = vi.fn(async (spec) =>
      makeSpec({ ...spec, id: spec.id ?? 'cloned-template', scope: 'user', name: spec.name }),
    );
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => [
          makeSpec({
            name: 'source-template',
            scope: 'user',
            id: 'source-template',
            description: 'Clone me',
            resourceConfig: { cpu: '1', memory: '2Gi' },
          }),
        ],
        saveLaunchSpec,
      },
    });

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /clone/i }));
    expect(screen.getByText('Clone catalog spec')).toBeInTheDocument();
    expect(screen.getByDisplayValue('source-template-copy')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(saveLaunchSpec).toHaveBeenCalledTimes(1));
    expect(saveLaunchSpec).toHaveBeenCalledWith(
      expect.objectContaining({
        id: undefined,
        name: 'source-template-copy',
        description: 'Clone me',
        resourceConfig: { cpu: '1', memory: '2Gi' },
      }),
    );
  });

  it('validates required names and shows save errors', async () => {
    const saveLaunchSpec = vi.fn(async () => {
      throw new Error('save rejected');
    });
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => [makeSpec({ name: 'starter' })],
        saveLaunchSpec,
      },
    });

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /new/i }));
    const nameInput = screen.getByDisplayValue('new-template');
    fireEvent.change(nameInput, { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    expect(screen.getByText('Name is required.')).toBeInTheDocument();
    expect(saveLaunchSpec).not.toHaveBeenCalled();

    fireEvent.change(nameInput, { target: { value: 'valid-template' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(screen.getByText('save rejected')).toBeInTheDocument());
    expect(saveLaunchSpec).toHaveBeenCalledTimes(1);
  });

  it('saves edits to git source and runtime fields', async () => {
    const saveLaunchSpec = vi.fn(async (spec) =>
      makeSpec({ ...spec, id: spec.id ?? 'git-template', scope: 'user', name: spec.name }),
    );
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => [
          makeSpec({
            name: 'git-template',
            source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'dev' },
          }),
        ],
        saveLaunchSpec,
      },
    });

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(screen.getByLabelText('Runtime definition'), {
      target: { value: 'skuldCodex' },
    });
    fireEvent.change(screen.getByLabelText('Workload type'), { target: { value: 'worker' } });
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Updated' } });
    fireEvent.change(screen.getByLabelText('System prompt'), {
      target: { value: 'Stay concise.' },
    });
    fireEvent.change(screen.getByLabelText('Git repo'), {
      target: { value: 'github.com/niuulabs/niuu' },
    });
    fireEvent.change(screen.getByLabelText('Branch'), { target: { value: 'main' } });
    fireEvent.change(screen.getByLabelText('Memory'), { target: { value: '8Gi' } });
    fireEvent.change(screen.getByLabelText('GPU'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(saveLaunchSpec).toHaveBeenCalledTimes(1));
    expect(saveLaunchSpec).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'spec-git-template',
        description: 'Updated',
        sessionDefinition: 'skuldCodex',
        workloadType: 'worker',
        systemPrompt: 'Stay concise.',
        resourceConfig: { memory: '8Gi', gpu: '1' },
        source: { type: 'git', repo: 'github.com/niuulabs/niuu', branch: 'main' },
      }),
    );
  });

  it('saves edits to local mount sources', async () => {
    const saveLaunchSpec = vi.fn(async (spec) =>
      makeSpec({ ...spec, id: spec.id ?? 'local-template', scope: 'user', name: spec.name }),
    );
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: async () => [
          makeSpec({
            name: 'local-template',
            source: { type: 'local_mount', local_path: '/workspace/volundr', paths: [] },
          }),
        ],
        saveLaunchSpec,
      },
    });

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    fireEvent.change(screen.getByLabelText('Local path'), {
      target: { value: '/workspace/niuu' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(saveLaunchSpec).toHaveBeenCalledTimes(1));
    expect(saveLaunchSpec).toHaveBeenCalledWith(
      expect.objectContaining({
        source: { type: 'local_mount', local_path: '/workspace/niuu', paths: [] },
      }),
    );
  });

  it('shows editable git and local source fields', async () => {
    renderWithSpecs([
      makeSpec({
        name: 'git-template',
        source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'dev' },
      }),
      makeSpec({
        name: 'local-template',
        source: { type: 'local_mount', local_path: '/workspace/volundr', paths: [] },
      }),
    ]);

    await waitForPage();
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    expect(screen.getByLabelText('Git repo')).toHaveValue('github.com/niuulabs/volundr');
    expect(screen.getByLabelText('Branch')).toHaveValue('dev');
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /edit catalog spec/i })).not.toBeInTheDocument(),
    );

    fireEvent.click(screen.getByText('local-template'));
    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    expect(screen.getByLabelText('Local path')).toHaveValue('/workspace/volundr');
  });
});
