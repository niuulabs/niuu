import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { createMockBifrostService, type BifrostModel } from '@niuulabs/plugin-bifrost';
import { QuickLaunch } from './QuickLaunch';
import { LaunchWizard } from './LaunchWizard';
import { LaunchCatalogPage } from './LaunchCatalogPage';
import { createMockVolundrService } from '../adapters/mock';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { FORGE_STANDARDS, selectedEffort, hostDefaultFolder } from './quickLaunchModel';

const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }));
const hosts = [
  {
    id: 'thor',
    name: 'Thor',
    slug: 'thor',
    baseUrl: 'http://100.66.123.128:8080',
    config: { defaultFolder: '/home/thor/repos' },
    enabled: true,
    isDefault: true,
    tags: [],
  },
  {
    id: 'spark',
    name: 'Spark',
    slug: 'spark',
    baseUrl: 'http://100.127.141.74:8080',
    config: { defaultFolder: '/home/xteo/repos' },
    enabled: true,
    isDefault: false,
    tags: [],
  },
];
const models = Object.fromEntries(
  FORGE_STANDARDS.flatMap((standard) =>
    standard.models.map((m): [string, BifrostModel] => [
      m.id,
      {
        ...m,
        enabled: true,
        provider: 'cloud',
        tier: 'frontier',
        color: '',
        description: '',
        supportsTools: true,
        supportsThinking: true,
        aliases: [],
        providerKeys: [],
        effortLevels: ['low', 'high', 'xhigh', 'ultra'],
        defaultEffort: 'high',
      },
    ]),
  ),
);
const miniModeFeatures = { localMountsEnabled: true, fileManagerEnabled: true, miniMode: true };
const clusterFeatures = { localMountsEnabled: false, fileManagerEnabled: true, miniMode: false };
function setup(
  overrides: Partial<ReturnType<typeof createMockVolundrService>> = {},
  ui?: React.ReactNode,
  catalog = models,
) {
  const base = createMockVolundrService();
  const startSession = vi.fn(base.startSession);
  const service = {
    ...base,
    startSession,
    getTargets: async () => hosts,
    // Thor and Spark are mini-mode Forge hosts that mount local folders.
    getFeatures: async () => miniModeFeatures,
    ...overrides,
  };
  const bifrost = { ...createMockBifrostService(), getModelCatalog: async () => catalog };
  const onAdvanced = vi.fn();
  const onCreated = vi.fn();
  renderWithVolundr(ui ?? <QuickLaunch onAdvanced={onAdvanced} onCreated={onCreated} />, {
    service,
    bifrost,
  });
  return { startSession, onAdvanced, onCreated };
}
async function ready() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeEnabled());
}
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe('QuickLaunch', () => {
  it('launches Claude tmux directly with local mount and effort, without resource requirements', async () => {
    const { startSession, onCreated } = setup();
    await ready();
    expect(screen.getByLabelText('Workspace source')).toHaveValue('local_mount');
    expect(screen.getByLabelText('Effort')).toHaveValue('xhigh');
    fireEvent.click(screen.getByText('Session details · optional'));
    fireEvent.change(screen.getByLabelText('Session name'), { target: { value: 'review-ui' } });
    fireEvent.change(screen.getByLabelText('Initial prompt'), {
      target: { value: 'Review the layout' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() => expect(startSession).toHaveBeenCalledOnce());
    expect(startSession).toHaveBeenCalledWith({
      name: 'review-ui',
      definition: 'skuldClaudeInteractive',
      model: 'claude-fable-5-1',
      instanceId: 'thor',
      // The connected Claude account and the non-Git integrations; no Git
      // account for a local folder.
      integrationIds: ['claude-code-setup', 'linear-main'],
      source: {
        type: 'local_mount',
        local_path: '/home/thor/repos',
        paths: [{ host_path: '/home/thor/repos', mount_path: '/workspace', read_only: false }],
      },
      workloadConfig: { reasoningEffort: 'xhigh' },
      initialPrompt: 'Review the layout',
    });
    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/volundr/sessions/$sessionId' }),
    );
  });
  it('uses Astra for Codex and allows GPT-6 Sol plus independent effort', async () => {
    const { startSession } = setup();
    await ready();
    fireEvent.click(screen.getByRole('button', { name: /Codex.*OpenAI/ }));
    expect(screen.getByLabelText('Model')).toHaveValue('gpt-6-astra');
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gpt-6-sol' } });
    fireEvent.change(screen.getByLabelText('Effort'), { target: { value: 'ultra' } });
    fireEvent.click(screen.getByRole('button', { name: 'Launch Codex' }));
    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          definition: 'skuldCodex',
          model: 'gpt-6-sol',
          workloadConfig: { reasoningEffort: 'ultra' },
        }),
      ),
    );
  });
  it('keeps folders on their owning hosts and uses a remembered per-host folder', async () => {
    localStorage.setItem('niuu.forge.launch.folder.spark', '/home/xteo/review');
    setup();
    await ready();
    fireEvent.change(screen.getByLabelText('Working folder'), {
      target: { value: '/home/thor/custom' },
    });
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'spark' } });
    expect(screen.getByLabelText('Working folder')).toHaveValue('/home/xteo/review');
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'thor' } });
    expect(screen.getByLabelText('Working folder')).toHaveValue('/home/thor/custom');
  });
  it('does not replace an unavailable requested model with another model', async () => {
    setup({}, undefined, {
      ...models,
      'claude-fable-5-1': { ...models['claude-fable-5-1']!, enabled: false },
    });
    await screen.findByText(/This standard is unavailable/);
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
    expect(screen.getByLabelText('Model')).toHaveValue('claude-fable-5-1');
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'claude-opus-5-5' } });
    await ready();
  });
  it('honours an explicitly empty effort list without sending a made-up effort', async () => {
    const { startSession } = setup({}, undefined, {
      ...models,
      'claude-fable-5-1': { ...models['claude-fable-5-1']!, effortLevels: [] },
    });
    await ready();
    expect(screen.getByLabelText('Effort')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() => expect(startSession).toHaveBeenCalledOnce());
    expect(startSession.mock.calls[0]![0]).not.toHaveProperty('workloadConfig');
  });
  it('blocks invalid folders and session names', async () => {
    setup();
    await ready();
    fireEvent.change(screen.getByLabelText('Working folder'), { target: { value: '~/wrong' } });
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Working folder'), { target: { value: '/valid' } });
    fireEvent.change(screen.getByLabelText('Session name'), { target: { value: 'UPPER CASE' } });
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
  });
  it('surfaces load failures and absence of hosts', async () => {
    setup({
      getTargets: async () => {
        throw new Error('Registry unreachable');
      },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Registry unreachable');
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
  });
  it('does not launch on a disabled host', async () => {
    setup({ getTargets: async () => hosts.map((h) => ({ ...h, enabled: false })) });
    await screen.findByText('Add an enabled host to launch a session.');
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
  });
  it('preserves the form and prevents duplicate submission while a launch is pending', async () => {
    let reject!: (reason: Error) => void;
    const startSession = vi.fn(
      () =>
        new Promise<never>((_, r) => {
          reject = r;
        }),
    );
    setup({ startSession });
    await ready();
    fireEvent.submit(screen.getByTestId('quick-launch-form'));
    fireEvent.submit(screen.getByTestId('quick-launch-form'));
    expect(startSession).toHaveBeenCalledOnce();
    expect(screen.getByLabelText('Host')).toBeDisabled();
    reject(new Error('Host is offline'));
    await screen.findByText('Host is offline');
    await ready();
    expect(screen.getByLabelText('Working folder')).toHaveValue('/home/thor/repos');
  });
  it('offers only Git on a Forge without local mounts and attaches the repository account', async () => {
    const { startSession } = setup({ getFeatures: async () => clusterFeatures });
    const source = await screen.findByLabelText('Workspace source');
    await waitFor(() => expect(source).toHaveValue('git'));
    expect(within(source).queryByRole('option', { name: 'Local mount' })).toBeNull();
    const repository = screen.getByLabelText('Repository');
    await waitFor(() =>
      expect(within(repository).getByRole('option', { name: /volundr/ })).toBeInTheDocument(),
    );
    fireEvent.change(repository, { target: { value: 'github.com/niuulabs/volundr' } });
    await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
          integrationIds: ['claude-code-setup', 'github-primary', 'linear-main'],
        }),
      ),
    );
  });
  it('re-derives a local folder source when switching to a Forge that cannot mount one', async () => {
    setup({
      getFeatures: async (instanceId?: string) =>
        instanceId === 'spark' ? clusterFeatures : miniModeFeatures,
    });
    await ready();
    fireEvent.change(screen.getByLabelText('Workspace source'), {
      target: { value: 'local_mount' },
    });
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'spark' } });
    await waitFor(() => expect(screen.getByLabelText('Workspace source')).toHaveValue('git'));
    expect(screen.queryByLabelText('Working folder')).toBeNull();
  });
  it('blocks the launch when the connected accounts cannot be read', async () => {
    setup({
      getIntegrations: async () => {
        throw new Error('Integrations unreachable');
      },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Integrations unreachable');
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
  });
  it('retains the advanced launch path', async () => {
    const { onAdvanced } = setup();
    await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Advanced launch' }));
    expect(onAdvanced).toHaveBeenCalledOnce();
  });
  it('opens quick launch from the session launcher and can switch to the legacy wizard', async () => {
    setup({}, <LaunchWizard open onOpenChange={vi.fn()} />);
    await ready();
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Quick launch');
    fireEvent.click(screen.getByRole('button', { name: 'Advanced launch' }));
    expect(await screen.findByRole('dialog')).toHaveAccessibleName('Launch pod');
  });
  it('opens a prefilled launch (Workspaces) straight in the advanced wizard', async () => {
    setup(
      {},
      <LaunchWizard
        open
        onOpenChange={vi.fn()}
        initialForm={{ sourcetype: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' }}
      />,
    );
    expect(await screen.findByRole('dialog')).toHaveAccessibleName('Launch pod');
    expect(screen.queryByTestId('quick-launch-form')).toBeNull();
  });
  it('keeps saved custom catalogues available behind the standards', async () => {
    setup({}, <LaunchCatalogPage />);
    await ready();
    expect(within(screen.getByLabelText('Launch standard')).getAllByRole('button')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: 'Manage custom catalogue' }));
    await screen.findByRole('button', { name: 'Back to standards' });
    fireEvent.click(screen.getByRole('button', { name: 'Back to standards' }));
    await ready();
  });
});

describe('QuickLaunch Forge tools', () => {
  it('offers both grants off and sends none unless one is switched on', async () => {
    const { startSession } = setup();
    await ready();
    expect(
      screen.getByRole('heading', { name: 'Forge tools for this session' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: /Manage sessions/ })).not.toBeChecked();
    expect(screen.getByRole('switch', { name: /Message sessions/ })).not.toBeChecked();
    expect(screen.getByText(/default grants apply/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() => expect(startSession).toHaveBeenCalledOnce());
    expect(startSession.mock.calls[0]?.[0]).not.toHaveProperty('forgeMcp');
  });

  it('sends the grants that are switched on, in a fixed order', async () => {
    const { startSession } = setup();
    await ready();
    fireEvent.click(screen.getByRole('switch', { name: /Message sessions/ }));
    fireEvent.click(screen.getByRole('switch', { name: /Manage sessions/ }));
    expect(screen.getByRole('switch', { name: /Manage sessions/ })).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({ forgeMcp: { grants: ['lifecycle', 'message'] } }),
      ),
    );
  });

  it('sends nothing again once a grant is switched back off', async () => {
    const { startSession } = setup();
    await ready();
    fireEvent.click(screen.getByRole('switch', { name: /Message sessions/ }));
    fireEvent.click(screen.getByRole('switch', { name: /Message sessions/ }));

    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() => expect(startSession).toHaveBeenCalledOnce());
    expect(startSession.mock.calls[0]?.[0]).not.toHaveProperty('forgeMcp');
  });
});

describe('launch policy helpers', () => {
  it('resolves effort from advertised metadata and folders from host configuration', () => {
    expect(selectedEffort(undefined, 'xhigh')).toBe('');
    expect(selectedEffort(models['gpt-6-astra'], 'invalid')).toBe('high');
    expect(selectedEffort({ ...models['gpt-6-astra']!, defaultEffort: '' }, 'invalid')).toBe(
      'ultra',
    );
    expect(hostDefaultFolder(undefined)).toBe('');
  });
});

describe('QuickLaunch runs as', () => {
  const offers = [
    {
      runtime: 'container' as const,
      executionProfile: 'vm-container@1',
      isDefault: true,
      grants: [],
      readiness: { state: 'warm' as const, warm: 1, remedy: null },
    },
    {
      runtime: 'openshell' as const,
      executionProfile: 'vm-openshell@1',
      isDefault: false,
      grants: [],
      readiness: { state: 'boot' as const, warm: 0, remedy: null },
    },
    {
      runtime: 'host' as const,
      executionProfile: 'vm-host@1',
      isDefault: false,
      grants: ['sudo' as const, 'docker' as const],
      readiness: { state: 'boot' as const, warm: 0, remedy: null },
    },
  ];
  const vmHost = (instanceId?: string) => (instanceId === 'spark' ? offers : []);

  it('offers no choice on a host that runs sessions one way', async () => {
    setup({ getSessionRuntimes: async (id?: string) => vmHost(id) });
    await ready();
    expect(screen.queryByRole('group', { name: 'Runs as' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('runs-as-readiness')).not.toBeInTheDocument();
  });

  it('launches with the runtime chosen on a VM host and says how soon it starts', async () => {
    const { startSession } = setup({ getSessionRuntimes: async (id?: string) => vmHost(id) });
    await ready();
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'spark' } });
    await screen.findByRole('group', { name: 'Runs as' });
    expect(screen.getByRole('button', { name: /Container/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByTestId('runs-as-readiness')).toHaveTextContent(
      'Free slot ready, starts in seconds',
    );
    fireEvent.click(screen.getByRole('button', { name: /OpenShell.*Policy sandbox/ }));
    expect(screen.getByTestId('runs-as-readiness')).toHaveTextContent(/a VM boots first/);
    await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          instanceId: 'spark',
          workloadConfig: { reasoningEffort: 'xhigh', execution_profile: 'vm-openshell@1' },
        }),
      ),
    );
  });

  it('sends extra access only for a runtime that offers it', async () => {
    const { startSession } = setup({ getSessionRuntimes: async (id?: string) => vmHost(id) });
    await ready();
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'spark' } });
    fireEvent.click(await screen.findByRole('button', { name: /On the VM/ }));
    expect(screen.getByText('Either one gives this session a VM to itself.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'sudo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Launch Claude' }));
    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          workloadConfig: {
            reasoningEffort: 'xhigh',
            execution_profile: 'vm-host@1',
            runtime_grants: ['sudo'],
          },
        }),
      ),
    );
  });

  it('refuses to launch when the host cannot say how it runs sessions', async () => {
    setup({
      getSessionRuntimes: async () => {
        throw new Error('forge down');
      },
    });
    expect(await screen.findByText(/Could not load launch options: .*forge down/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
  });
});
