import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { createMockBifrostService, type BifrostModel } from '@niuulabs/plugin-bifrost';
import { QuickLaunch } from './QuickLaunch';
import { LaunchWizard } from './LaunchWizard';
import { LaunchCatalogPage } from './LaunchCatalogPage';
import { createMockVolundrService } from '../adapters/mock';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import {
  FORGE_STANDARDS,
  normalizeForgeOrigin,
  selectedEffort,
  hostDefaultFolder,
} from './quickLaunchModel';

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
function setup(
  overrides: Partial<ReturnType<typeof createMockVolundrService>> = {},
  ui?: React.ReactNode,
  catalog = models,
) {
  const base = createMockVolundrService();
  const startSession = vi.fn(base.startSession);
  const service = { ...base, startSession, getForgeHosts: async () => hosts, ...overrides };
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
  it('uses Astra for Codex and allows Sol plus independent effort', async () => {
    const { startSession } = setup();
    await ready();
    fireEvent.click(screen.getByRole('button', { name: /Codex.*OpenAI/ }));
    expect(screen.getByLabelText('Model')).toHaveValue('gpt-6-astra');
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gpt-5.6-sol' } });
    fireEvent.change(screen.getByLabelText('Effort'), { target: { value: 'ultra' } });
    fireEvent.click(screen.getByRole('button', { name: 'Launch Codex' }));
    await waitFor(() =>
      expect(startSession).toHaveBeenCalledWith(
        expect.objectContaining({
          definition: 'skuldCodex',
          model: 'gpt-5.6-sol',
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
    fireEvent.change(screen.getByLabelText('Forge'), { target: { value: 'spark' } });
    expect(screen.getByLabelText('Working folder')).toHaveValue('/home/xteo/review');
    fireEvent.change(screen.getByLabelText('Forge'), { target: { value: 'thor' } });
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
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'claude-opus-5' } });
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
      getForgeHosts: async () => {
        throw new Error('Registry unreachable');
      },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Registry unreachable');
    expect(screen.getByRole('button', { name: 'Launch Claude' })).toBeDisabled();
  });
  it('does not launch on a disabled host', async () => {
    setup({ getForgeHosts: async () => hosts.map((h) => ({ ...h, enabled: false })) });
    await screen.findByText('Add an enabled Forge host to launch a session.');
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
    expect(screen.getByLabelText('Forge')).toBeDisabled();
    reject(new Error('Host is offline'));
    await screen.findByText('Host is offline');
    await ready();
    expect(screen.getByLabelText('Working folder')).toHaveValue('/home/thor/repos');
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

describe('launch policy helpers', () => {
  it('accepts plain IPs, IPv6 and HTTPS origins, rejects non-origins', () => {
    expect(normalizeForgeOrigin(' 100.66.123.128 ')).toBe('http://100.66.123.128:8080');
    expect(normalizeForgeOrigin('[::1]:8080')).toBe('http://[::1]:8080');
    expect(normalizeForgeOrigin('https://forge.example/')).toBe('https://forge.example');
    for (const value of [
      '',
      'hello world',
      'http://[invalid',
      'file:///tmp',
      'https://u:p@forge',
      'https://forge/api',
      'https://forge/?a=1',
      'https://forge/#a',
    ])
      expect(() => normalizeForgeOrigin(value)).toThrow();
  });
  it('resolves effort from advertised metadata and folders from host configuration', () => {
    expect(selectedEffort(undefined, 'xhigh')).toBe('');
    expect(selectedEffort(models['gpt-6-astra'], 'invalid')).toBe('high');
    expect(selectedEffort({ ...models['gpt-6-astra']!, defaultEffort: '' }, 'invalid')).toBe(
      'ultra',
    );
    expect(hostDefaultFolder(undefined)).toBe('');
  });
});
