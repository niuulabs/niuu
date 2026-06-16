import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
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

    expect(screen.getByText(/loading launch catalog/i)).toBeInTheDocument();
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

    await waitFor(() =>
      expect(screen.getByText(/failed to load launch catalog/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('catalog unavailable')).toBeInTheDocument();
  });

  it('renders a generic error message for non-Error rejections', async () => {
    renderWithVolundr(<LaunchCatalogPage />, {
      service: {
        ...createMockVolundrService(),
        getLaunchSpecs: () => Promise.reject('string failure'),
      },
    });

    await waitFor(() =>
      expect(screen.getByText(/failed to load launch catalog/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('Unknown error')).toBeInTheDocument();
  });

  it('renders the empty state when no specs are configured', async () => {
    renderWithSpecs([]);

    await waitForPage();
    expect(screen.getByText(/no launch specs configured yet/i)).toBeInTheDocument();
  });

  it('renders header copy and one card per spec', async () => {
    renderWithSpecs([makeSpec({ name: 'alpha-spec' }), makeSpec({ name: 'beta-spec' })]);

    await waitForPage();
    expect(screen.getByText('Launch Catalog')).toBeInTheDocument();
    expect(screen.getByText(/preloaded catalog specs/i)).toBeInTheDocument();
    expect(screen.getByText('alpha-spec')).toBeInTheDocument();
    expect(screen.getByText('beta-spec')).toBeInTheDocument();
    expect(screen.queryByText(/no launch specs configured yet/i)).not.toBeInTheDocument();
  });

  it('renders specs from the default mock service', async () => {
    renderWithVolundr(<LaunchCatalogPage />);

    await waitForPage();
    expect(screen.getAllByRole('article').length).toBeGreaterThan(0);
  });

  it('shows the default badge only for default specs', async () => {
    renderWithSpecs([
      makeSpec({ name: 'default-spec', isDefault: true, workloadType: 'worker' }),
      makeSpec({ name: 'plain-spec', isDefault: false, workloadType: 'worker' }),
    ]);

    await waitForPage();
    expect(screen.getByText('default')).toBeInTheDocument();
    const plainCard = screen.getByText('plain-spec').closest('article');
    expect(plainCard).not.toBeNull();
    expect(within(plainCard!).queryByText('default')).not.toBeInTheDocument();
  });

  it('falls back to placeholder copy when description is empty', async () => {
    renderWithSpecs([
      makeSpec({ name: 'described', description: 'Runs the nightly batch.' }),
      makeSpec({ name: 'undescribed', description: '' }),
    ]);

    await waitForPage();
    expect(screen.getByText('Runs the nightly batch.')).toBeInTheDocument();
    expect(screen.getByText('No description configured.')).toBeInTheDocument();
  });

  it('shows the scope label on each card', async () => {
    renderWithSpecs([
      makeSpec({ name: 'sys-spec', scope: 'system', id: null }),
      makeSpec({ name: 'user-spec', scope: 'user' }),
    ]);

    await waitForPage();
    expect(screen.getByText('system')).toBeInTheDocument();
    expect(screen.getByText('user')).toBeInTheDocument();
  });

  it('prefers sessionDefinition over workloadType for the runtime field', async () => {
    renderWithSpecs([
      makeSpec({ name: 'with-def', sessionDefinition: 'skuldClaude', workloadType: 'default' }),
      makeSpec({ name: 'without-def', sessionDefinition: null, workloadType: 'batch-runner' }),
    ]);

    await waitForPage();
    expect(screen.getByText('skuldClaude')).toBeInTheDocument();
    expect(screen.getByText('batch-runner')).toBeInTheDocument();
  });

  it('renders the model or a launch-time placeholder', async () => {
    renderWithSpecs([
      makeSpec({ name: 'with-model', model: 'claude-sonnet' }),
      makeSpec({ name: 'without-model', model: null }),
    ]);

    await waitForPage();
    expect(screen.getByText('claude-sonnet')).toBeInTheDocument();
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
    expect(screen.getByText('cpu 4 · mem 8Gi · gpu 1')).toBeInTheDocument();
  });

  it('omits gpu when it is "0" and falls back when nothing is set', async () => {
    renderWithSpecs([
      makeSpec({ name: 'zero-gpu', resourceConfig: { cpu: '2', memory: '4Gi', gpu: '0' } }),
      makeSpec({ name: 'no-resources', resourceConfig: {} }),
    ]);

    await waitForPage();
    expect(screen.getByText('cpu 2 · mem 4Gi')).toBeInTheDocument();
    expect(screen.getByText('default resources')).toBeInTheDocument();
  });

  it('formats partial resource config without cpu', async () => {
    renderWithSpecs([makeSpec({ name: 'mem-only', resourceConfig: { memory: '2Gi' } })]);

    await waitForPage();
    expect(screen.getByText('mem 2Gi')).toBeInTheDocument();
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
    expect(screen.getByText('github.com/niuulabs/volundr @ main')).toBeInTheDocument();
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
    expect(screen.getByText('mimir')).toBeInTheDocument();
    expect(screen.getByText('bifrost')).toBeInTheDocument();
  });
});
