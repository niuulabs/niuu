import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GuildPage } from './GuildPage';
import { canManageInstance, type InstanceRecord } from './guildInstances';

const identity = {
  userId: 'me',
  tenantId: 'team',
  roles: ['volundr:admin'],
  displayName: 'Me',
  email: '',
  status: 'active',
};
vi.mock('@niuulabs/plugin-sdk', async (original) => ({
  ...(await original<object>()),
  useConfig: () => ({ services: { niuu: { mode: 'http', baseUrl: '/api/v1/niuu' } } }),
  useService: () => ({ getIdentity: async () => identity }),
}));
vi.mock('@tanstack/react-router', () => ({ useRouterState: () => ({ search: '' }) }));

const node: InstanceRecord = {
  id: 'node-1',
  kind: 'volundr',
  name: 'build-kit',
  slug: 'build-kit',
  baseUrl: 'https://build-kit.test',
  visibility: 'tenant',
  ownerId: null,
  tenantId: 'team',
  enabled: true,
  isDefault: false,
  tags: ['build'],
  config: {
    transport: 'remote',
    defaultFolder: '/home/build',
    credentialBinding: { scope: 'tenant', name: 'forge' },
    capabilities: ['sessions'],
  },
  createdAt: '2026-09-18T10:00:00Z',
  updatedAt: '2026-09-18T10:00:00Z',
};
let records: InstanceRecord[];
let failure = false;
let probeFailure = false;
let probes = 0;
let loading: Promise<void> | undefined;
let mutations: Array<{ method: string; url: string; body: Record<string, unknown> | null }>;
function renderGuild() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <GuildPage />
    </QueryClientProvider>,
  );
  return client;
}
beforeEach(() => {
  records = [structuredClone(node)];
  failure = false;
  probeFailure = false;
  probes = 0;
  loading = undefined;
  mutations = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      const method = init.method ?? 'GET';
      if (url.endsWith('/test')) {
        probes += 1;
        return probeFailure
          ? Response.json({ detail: 'Probe unavailable' }, { status: 503 })
          : Response.json({ ok: true, message: 'Connected' });
      }
      if (method !== 'GET') {
        const body = init.body ? JSON.parse(String(init.body)) : null;
        mutations.push({ method, url, body });
        if (failure) return Response.json({ detail: 'Registry access denied' }, { status: 403 });
        if (method === 'DELETE') {
          records = [];
          return new Response(null, { status: 204 });
        }
        records = [{ ...records[0]!, ...body }];
        return Response.json(records[0]);
      }
      if (url.endsWith('/instances')) {
        await loading;
        return Response.json(records);
      }
      if (url.includes('/credentials/')) return Response.json({ credentials: [] });
      return Response.json([]);
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe('Guild node management', () => {
  it('shows loading before the registry is available', async () => {
    let finish!: () => void;
    loading = new Promise((resolve) => {
      finish = resolve;
    });
    renderGuild();
    expect(screen.getByText('Loading registry…')).toBeInTheDocument();
    finish();
    expect(await screen.findByRole('button', { name: 'Edit settings' })).toBeEnabled();
  });
  it('edits the selected node through PATCH, preserving transport and credentials', async () => {
    const client = renderGuild();
    client.setQueryData(['volundr', 'targets'], []);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit settings' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Build Kit' } });
    fireEvent.change(screen.getByLabelText(/^Server URL/), {
      target: { value: 'http://100.90.20.64:8080/' },
    });
    fireEvent.change(screen.getByLabelText('Default local folder'), { target: { value: '/work' } });
    fireEvent.change(screen.getByLabelText('Tags'), { target: { value: 'build, fast fast' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mutations).toEqual([
      {
        method: 'PATCH',
        url: '/api/v1/niuu/instances/node-1',
        body: {
          name: 'Build Kit',
          slug: 'build-kit',
          baseUrl: 'http://100.90.20.64:8080',
          enabled: true,
          isDefault: false,
          tags: ['build', 'fast'],
          config: { ...node.config, defaultFolder: '/work' },
        },
      },
    ]);
    expect(client.getQueryState(['volundr', 'targets'])?.isInvalidated).toBe(true);
    expect(screen.getByRole('heading', { name: 'Build Kit', level: 2 })).toBeInTheDocument();
  });
  it('keeps unsaved changes after API failure and supports retry', async () => {
    renderGuild();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit settings' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Corrected name' } });
    failure = true;
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Registry access denied');
    expect(screen.getByLabelText('Name')).toHaveValue('Corrected name');
    failure = false;
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
  it('rejects invalid config and endpoints without issuing mutations', async () => {
    renderGuild();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit settings' }));
    fireEvent.change(screen.getByLabelText(/^Configuration/), { target: { value: '[]' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(screen.getByRole('alert')).toHaveTextContent('JSON object');
    fireEvent.change(screen.getByLabelText(/^Configuration/), { target: { value: '{}' } });
    fireEvent.change(screen.getByLabelText(/^Server URL/), {
      target: { value: 'http://build.test/volundr?config=/config.live.json' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(screen.getByRole('alert')).toHaveTextContent(
      'without credentials, a query, or a fragment',
    );
    expect(mutations).toHaveLength(0);
  });
  it('cancels deletion, reports errors, then removes only the selected registration', async () => {
    renderGuild();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete node' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Its server and sessions keep running');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(mutations).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Delete node' }));
    failure = true;
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete node' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('Registry access denied');
    failure = false;
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete node' }),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByTestId('guild-instance-card-build-kit')).not.toBeInTheDocument();
    expect(
      mutations.every(
        (entry) => entry.method === 'DELETE' && entry.url === '/api/v1/niuu/instances/node-1',
      ),
    ).toBe(true);
  });
  it('saves advanced settings and clears an optional default folder', async () => {
    renderGuild();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit settings' }));
    fireEvent.change(screen.getByLabelText('Default local folder'), { target: { value: '' } });
    fireEvent.click(screen.getByLabelText('Enabled'));
    fireEvent.click(screen.getByLabelText('Default node'));
    fireEvent.change(screen.getByLabelText(/^Routing slug/), { target: { value: 'renamed-kit' } });
    fireEvent.change(screen.getByLabelText('Visibility'), { target: { value: 'user' } });
    fireEvent.change(screen.getByLabelText(/^Configuration/), {
      target: { value: '{"transport":"remote","capabilities":["sessions","logs"]}' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mutations[0]!.body).toMatchObject({
      enabled: false,
      isDefault: true,
      slug: 'renamed-kit',
      visibility: 'user',
      config: { transport: 'remote', capabilities: ['sessions', 'logs'] },
    });
    expect(mutations[0]!.body!.config).not.toHaveProperty('defaultFolder');
  });
  it('disables management of a system node for a non-admin', async () => {
    const roles = identity.roles;
    identity.roles = [];
    records[0]!.visibility = 'system';
    try {
      renderGuild();
      expect(await screen.findByRole('button', { name: 'Edit settings' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Delete node' })).toBeDisabled();
    } finally {
      identity.roles = roles;
    }
  });
  it('shows a failed health request and retries only on demand', async () => {
    probeFailure = true;
    renderGuild();
    expect(await screen.findAllByText('Probe unavailable')).not.toHaveLength(0);
    fireEvent.change(screen.getByPlaceholderText('filter by name, endpoint, tag, or auth…'), {
      target: { value: 'build' },
    });
    expect(probes).toBe(1);
    probeFailure = false;
    fireEvent.click(screen.getByRole('button', { name: 'test endpoint' }));
    await waitFor(() => expect(probes).toBe(2));
  });
  it('matches owner, tenant and administrator management permissions', () => {
    const member = { ...identity, roles: [] };
    expect(canManageInstance(node, member)).toBe(true);
    expect(canManageInstance({ ...node, tenantId: 'other' }, member)).toBe(false);
    expect(canManageInstance({ ...node, visibility: 'user', ownerId: 'me' }, member)).toBe(true);
    expect(canManageInstance({ ...node, visibility: 'user', ownerId: 'other' }, member)).toBe(
      false,
    );
    expect(canManageInstance({ ...node, visibility: 'system' }, member)).toBe(false);
    expect(canManageInstance({ ...node, visibility: 'system' }, identity)).toBe(true);
    expect(canManageInstance(node)).toBe(false);
  });
});
