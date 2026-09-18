import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn<(path: string) => Promise<unknown>>(),
  patch: vi.fn(async () => null),
  post: vi.fn<(path: string, body?: unknown) => Promise<unknown>>(async (path: string) => {
    if (path === '/api/v1/tokens') {
      return {
        id: 'tok-2',
        name: 'ci-runner',
        token: 'niuu_pat_secret',
        createdAt: '2026-05-15T12:30:00Z',
      };
    }
    return null;
  }),
  delete: vi.fn(async () => null),
}));

function installDefaultGetMock() {
  apiMocks.get.mockImplementation(async (path: string) => {
    if (path === '/settings') {
      return {
        title: 'Ting',
        subtitle: 'saga coordinator settings',
        scope: 'service',
        sections: [
          {
            id: 'general',
            label: 'General',
            description: 'Core service bindings for the coordinator.',
            fields: [
              {
                key: 'service_name',
                label: 'Service',
                type: 'text',
                value: 'Ting',
                readOnly: true,
              },
            ],
          },
          {
            id: 'notifications',
            label: 'Notifications',
            description: 'Operator alerts',
            path: '/settings/notifications',
            saveLabel: 'Save notification settings',
            fields: [
              {
                key: 'enabled',
                label: 'Enabled',
                type: 'boolean',
                value: true,
                readOnly: false,
              },
            ],
          },
        ],
      };
    }
    if (path === '/api/v1/tokens') {
      return [
        { id: 'tok-1', name: 'local-tools', createdAt: '2026-05-15T12:00:00Z', lastUsedAt: null },
      ];
    }
    if (path === '/api/v1/credentials/user') {
      return {
        credentials: [
          {
            id: 'cred-1',
            name: 'shared-openai',
            secretType: 'api_key',
            keys: ['api_key'],
          },
        ],
      };
    }
    if (path === '/api/v1/credentials/types') {
      return [
        {
          type: 'api_key',
          label: 'API Key',
          description: 'API keys for external services',
          fields: [{ key: 'api_key', label: 'API Key', type: 'password', required: true }],
        },
      ];
    }
    if (path === '/api/v1/integrations') {
      return [
        {
          id: 'int-1',
          slug: 'telegram',
          integrationType: 'messaging',
          credentialName: 'telegram-main',
          enabled: true,
        },
      ];
    }
    if (path === '/api/v1/integrations/catalog') {
      return [
        {
          id: 'telegram',
          slug: 'telegram',
          name: 'Telegram',
          description: 'Bot notifications',
          integration_type: 'messaging',
          adapter: '',
          auth_type: 'api_key',
          credential_schema: {
            required: ['bot_token', 'chat_id'],
            properties: {
              bot_token: { label: 'Bot token', type: 'password' },
              chat_id: { label: 'Chat ID', type: 'string' },
            },
          },
          config_schema: {},
        },
      ];
    }
    throw new Error(`Unexpected GET ${path}`);
  });
}

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  params: { providerId: 'identity', sectionId: 'tokens' },
}));

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: any) => (
    <a href={String(to)} {...props}>
      {children}
    </a>
  ),
  useParams: () => routerMocks.params,
  useRouter: () => ({ navigate: routerMocks.navigate }),
}));

vi.mock('@niuulabs/query', () => ({
  createApiClient: vi.fn(() => ({
    get: apiMocks.get,
    patch: apiMocks.patch,
    post: apiMocks.post,
    delete: apiMocks.delete,
  })),
}));

function wrap(children: ReactNode, enableSessions = false) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <ConfigProvider
      value={{
        demoMode: false,
        theme: 'ice',
        plugins: {
          volundr: { enabled: enableSessions, order: 4 },
          login: { enabled: true, order: 0 },
          credentials: { enabled: true, order: 1 },
          integrations: { enabled: true, order: 2 },
          ting: { enabled: true, order: 3 },
        },
        services: {
          identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/identity' },
          credentials: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/credentials' },
          integrations: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/integrations' },
          ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
        },
      }}
    >
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ConfigProvider>,
  );
}

describe('SettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installDefaultGetMock();
  });

  it('renders local session checkboxes without fetching or saving a remote schema', () => {
    localStorage.clear();
    routerMocks.params = { providerId: 'session-view', sectionId: 'tabs' };
    wrap(<SettingsPage />, true);
    const terminal = screen.getByRole('checkbox', { name: 'Terminal' }) as HTMLInputElement;
    expect(terminal.checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: /Chat/ }) as HTMLInputElement).disabled).toBe(
      true,
    );
    fireEvent.click(terminal);
    expect(terminal.checked).toBe(true);
    expect(localStorage.getItem('niuu.forge.sessionTabs')).toContain('terminal');
    expect(apiMocks.get).not.toHaveBeenCalled();
    expect(apiMocks.patch).not.toHaveBeenCalled();
  });

  it('renders PAT management in the unified shell without the old aggregate copy', async () => {
    routerMocks.params = { providerId: 'identity', sectionId: 'tokens' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'You',
          subtitle: 'personal settings',
          scope: 'user',
          sections: [
            {
              id: 'profile',
              label: 'Profile',
              description: 'Identity profile.',
              fields: [
                {
                  key: 'email',
                  label: 'Email',
                  type: 'text',
                  value: 'admin@example.com',
                  readOnly: true,
                },
              ],
            },
            {
              id: 'tokens',
              label: 'Personal access tokens',
              description: 'Create and revoke tokens.',
              fields: [],
              resources: [
                {
                  id: 'personal_access_tokens',
                  type: 'tokens',
                  label: 'Personal access tokens',
                  description: 'Tokens are shown once when created.',
                  listPath: '/api/v1/tokens',
                  createPath: '/api/v1/tokens',
                  deletePath: '/api/v1/tokens/{id}',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/tokens') {
        return [
          {
            id: 'tok-1',
            name: 'local-tools',
            createdAt: '2026-05-15T12:00:00Z',
            lastUsedAt: null,
          },
        ];
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    wrap(<SettingsPage />);

    expect(screen.queryByText(/Aggregated from/i)).toBeNull();
    expect((await screen.findAllByRole('heading', { name: 'Personal access tokens' })).length).toBe(
      2,
    );
    expect(screen.getByText('Create token')).toBeTruthy();
    expect(await screen.findByText('local-tools')).toBeTruthy();
    const createButton = screen.getByRole('button', { name: 'Create token' }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText('Token name'), { target: { value: 'ci-runner' } });
    expect(createButton.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Permission'), {
      target: { value: 'forge:session:create' },
    });
    fireEvent.click(createButton);
    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith('/api/v1/tokens', {
        name: 'ci-runner',
        scopes: ['forge:session:create'],
      }),
    );

    expect(screen.getByText('Editable')).toBeTruthy();
  });

  it('renders a visible editable checkbox control for boolean fields', async () => {
    routerMocks.params = { providerId: 'ting', sectionId: 'notifications' };
    wrap(<SettingsPage />);

    expect(await screen.findByRole('heading', { name: 'Notifications' })).toBeTruthy();
    expect(screen.getByText('Editable')).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'Enabled' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Save notification settings' })).toBeTruthy();
  });

  it('saves a section as flat field values to the section path', async () => {
    routerMocks.params = { providerId: 'ting', sectionId: 'notifications' };
    apiMocks.patch.mockResolvedValueOnce(null);
    wrap(<SettingsPage />);

    const checkbox = await screen.findByRole('checkbox', { name: 'Enabled' });
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: 'Save notification settings' }));

    await waitFor(() => {
      expect(apiMocks.patch).toHaveBeenCalledWith('/settings/notifications', { enabled: false });
    });
    expect(await screen.findByText('Saved.')).toBeTruthy();
  });

  it('shows the server reason when a section save fails', async () => {
    routerMocks.params = { providerId: 'ting', sectionId: 'notifications' };
    apiMocks.patch.mockRejectedValueOnce(
      Object.assign(new Error('API request failed: 422'), {
        detail: 'send homeEnabled and/or fileManagerEnabled; nothing to update',
      }),
    );
    wrap(<SettingsPage />);

    await screen.findByRole('checkbox', { name: 'Enabled' });
    fireEvent.click(screen.getByRole('button', { name: 'Save notification settings' }));

    expect((await screen.findByTestId('settings-save-error')).textContent).toBe(
      'Failed to save this section: send homeEnabled and/or fileManagerEnabled; nothing to update',
    );
  });

  it('renders the credentials resource composer inside the unified shell', async () => {
    routerMocks.params = { providerId: 'credentials', sectionId: 'user' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Credentials',
          subtitle: 'stored secrets and runtime keys',
          scope: 'user',
          sections: [
            {
              id: 'user',
              label: 'User credentials',
              description: 'Reusable stored credentials.',
              fields: [],
              resources: [
                {
                  id: 'user_credentials',
                  type: 'credentials',
                  label: 'User credentials',
                  description: 'Store secrets once and reuse them.',
                  listPath: '/api/v1/credentials/user',
                  typesPath: '/api/v1/credentials/types',
                  createPath: '/api/v1/credentials/user',
                  deletePath: '/api/v1/credentials/user/{name}',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/user') {
        return {
          credentials: [
            {
              id: 'cred-1',
              name: 'shared-openai',
              secretType: 'api_key',
              keys: ['api_key'],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/types') {
        return [
          {
            type: 'api_key',
            label: 'API Key',
            description: 'API keys for external services',
            fields: [{ key: 'api_key', label: 'API Key', type: 'password', required: true }],
          },
        ];
      }
      throw new Error(`Unexpected GET ${path}`);
    });
    wrap(<SettingsPage />);

    expect((await screen.findAllByRole('heading', { name: 'User credentials' })).length).toBe(2);
    expect(screen.getByText('Store credential')).toBeTruthy();
    expect(await screen.findByText('shared-openai')).toBeTruthy();
  });

  it('renders the integrations resource composer inside the unified shell', async () => {
    routerMocks.params = { providerId: 'integrations', sectionId: 'connections' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Integrations',
          subtitle: 'connected services and providers',
          scope: 'user',
          sections: [
            {
              id: 'connections',
              label: 'Connections',
              description: 'Connect external services.',
              fields: [],
              resources: [
                {
                  id: 'integration_connections',
                  type: 'integrations',
                  label: 'Integration connections',
                  description: 'Connect services and providers.',
                  listPath: '/api/v1/integrations',
                  catalogPath: '/api/v1/integrations/catalog',
                  createPath: '/api/v1/integrations',
                  deletePath: '/api/v1/integrations/{id}',
                  credentialListPath: '/api/v1/credentials/user',
                  testPath: '/api/v1/integrations/{id}/test',
                  oauthAuthorizePath: '/api/v1/integrations/oauth/{slug}/authorize',
                  oauthDisconnectPath: '/api/v1/integrations/oauth/{slug}/disconnect',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/user') {
        return {
          credentials: [
            {
              id: 'cred-1',
              name: 'shared-openai',
              secretType: 'api_key',
              keys: ['api_key'],
            },
          ],
        };
      }
      if (path === '/api/v1/integrations') {
        return [
          {
            id: 'int-1',
            slug: 'telegram',
            integrationType: 'messaging',
            credentialName: 'telegram-main',
            enabled: true,
          },
        ];
      }
      if (path === '/api/v1/integrations/catalog') {
        return [
          {
            id: 'telegram',
            slug: 'telegram',
            name: 'Telegram',
            description: 'Bot notifications',
            integration_type: 'messaging',
            adapter: '',
            auth_type: 'api_key',
            credential_schema: {
              required: ['bot_token', 'chat_id'],
              properties: {
                bot_token: { label: 'Bot token', type: 'password' },
                chat_id: { label: 'Chat ID', type: 'string' },
              },
            },
            config_schema: {},
          },
        ];
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    wrap(<SettingsPage />);

    expect(await screen.findByRole('heading', { name: 'Connections' })).toBeTruthy();
    expect(screen.getByText('Integration connections')).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Connect integration' })).toBeTruthy();
    expect(await screen.findAllByText('Telegram')).toHaveLength(2);
    expect(screen.getByDisplayValue('telegram-credential')).toBeTruthy();
  });

  it.each(['Linear-mcp', ''])('shows the MCP connection name or endpoint: %s', async (name) => {
    routerMocks.params = { providerId: 'integrations', sectionId: 'connections' };
    const defaultGet = apiMocks.get.getMockImplementation()!;
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Integrations',
          scope: 'user',
          sections: [
            {
              id: 'connections',
              label: 'Connections',
              fields: [],
              resources: [
                {
                  id: 'integration_connections',
                  type: 'integrations',
                  label: 'Integration connections',
                  listPath: '/api/v1/integrations',
                  catalogPath: '/api/v1/integrations/catalog',
                  credentialListPath: '/api/v1/credentials/user',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/integrations') {
        return [
          {
            id: 'linear-mcp',
            slug: 'mcp',
            integration_type: 'mcp',
            credential_name: 'mcp-internal-credential-id',
            enabled: true,
            config: { name, mcp_url: 'https://mcp.linear.app/mcp/readonly' },
          },
        ];
      }
      return defaultGet(path);
    });
    wrap(<SettingsPage />);
    expect(
      await screen.findByText(name || 'https://mcp.linear.app/mcp/readonly', { exact: true }),
    ).toBeInTheDocument();
    expect(screen.getByText(/mcp · https:\/\/mcp.linear.app\/mcp\/readonly/)).toBeInTheDocument();
    expect(screen.queryByText(/mcp-internal-credential-id/)).not.toBeInTheDocument();
  });

  it('supports reusing an existing stored credential for non-oauth integrations', async () => {
    routerMocks.params = { providerId: 'integrations', sectionId: 'connections' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Integrations',
          subtitle: 'connected services and providers',
          scope: 'user',
          sections: [
            {
              id: 'connections',
              label: 'Connections',
              description: 'Connect external services.',
              fields: [],
              resources: [
                {
                  id: 'integration_connections',
                  type: 'integrations',
                  label: 'Integration connections',
                  description: 'Connect services and providers.',
                  listPath: '/api/v1/integrations',
                  catalogPath: '/api/v1/integrations/catalog',
                  createPath: '/api/v1/integrations',
                  deletePath: '/api/v1/integrations/{id}',
                  credentialListPath: '/api/v1/credentials/user',
                  testPath: '/api/v1/integrations/{id}/test',
                  oauthAuthorizePath: '/api/v1/integrations/oauth/{slug}/authorize',
                  oauthDisconnectPath: '/api/v1/integrations/oauth/{slug}/disconnect',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/user') {
        return {
          credentials: [
            {
              id: 'cred-1',
              name: 'shared-linear',
              secretType: 'api_key',
              keys: ['api_key'],
            },
          ],
        };
      }
      if (path === '/api/v1/integrations') {
        return [];
      }
      if (path === '/api/v1/integrations/catalog') {
        return [
          {
            id: 'linear',
            slug: 'linear',
            name: 'Linear',
            description: 'Issue tracking',
            integration_type: 'issue_tracker',
            adapter: 'linear',
            auth_type: 'api_key',
            credential_schema: {
              required: ['api_key'],
              properties: {
                api_key: { label: 'API Key', type: 'password' },
              },
            },
            config_schema: {},
          },
        ];
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    wrap(<SettingsPage />);

    expect(await screen.findByRole('heading', { name: 'Connections' })).toBeTruthy();
    await screen.findByRole('button', { name: 'Connect integration' });
    const reuseToggle = await screen.findByRole('checkbox', {
      name: /Create a new credential/i,
    });
    fireEvent.click(reuseToggle);
    expect(screen.getByText('Stored credential')).toBeTruthy();
    expect(screen.getByRole('option', { name: 'shared-linear' })).toBeTruthy();
  });

  it('renders oauth integration flows without the inline credential composer', async () => {
    routerMocks.params = { providerId: 'integrations', sectionId: 'connections' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Integrations',
          subtitle: 'connected services and providers',
          scope: 'user',
          sections: [
            {
              id: 'connections',
              label: 'Connections',
              description: 'Connect external services.',
              fields: [],
              resources: [
                {
                  id: 'integration_connections',
                  type: 'integrations',
                  label: 'Integration connections',
                  description: 'Connect services and providers.',
                  listPath: '/api/v1/integrations',
                  catalogPath: '/api/v1/integrations/catalog',
                  createPath: '/api/v1/integrations',
                  deletePath: '/api/v1/integrations/{id}',
                  credentialListPath: '/api/v1/credentials/user',
                  testPath: '/api/v1/integrations/{id}/test',
                  oauthAuthorizePath: '/api/v1/integrations/oauth/{slug}/authorize',
                  oauthDisconnectPath: '/api/v1/integrations/oauth/{slug}/disconnect',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/user') {
        return { credentials: [] };
      }
      if (path === '/api/v1/integrations') {
        return [
          {
            id: 'int-1',
            slug: 'github',
            integrationType: 'source_control',
            credentialName: 'github-oauth',
            enabled: true,
          },
        ];
      }
      if (path === '/api/v1/integrations/catalog') {
        return [
          {
            id: 'github',
            slug: 'github',
            name: 'GitHub',
            description: 'Source control',
            integration_type: 'source_control',
            adapter: 'github',
            auth_type: 'oauth_token',
            credential_schema: {},
            config_schema: {},
          },
        ];
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    wrap(<SettingsPage />);

    expect(await screen.findByRole('button', { name: 'Connect with OAuth' })).toBeTruthy();
    expect(screen.getByText('Connected as github-oauth')).toBeTruthy();
    expect(screen.queryByText('Create a new credential')).toBeNull();
  });

  it('offers Jira sign-in or API token setup with project and label scope', async () => {
    routerMocks.params = { providerId: 'integrations', sectionId: 'connections' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Integrations',
          scope: 'user',
          sections: [
            {
              id: 'connections',
              label: 'Connections',
              fields: [],
              resources: [
                {
                  id: 'integration_connections',
                  type: 'integrations',
                  label: 'Integration connections',
                  listPath: '/api/v1/integrations',
                  catalogPath: '/api/v1/integrations/catalog',
                  createPath: '/api/v1/integrations',
                  deletePath: '/api/v1/integrations/{id}',
                  credentialListPath: '/api/v1/credentials/user',
                  testPath: '/api/v1/integrations/{id}/test',
                  oauthAuthorizePath: '/api/v1/integrations/oauth/{slug}/authorize',
                  oauthDisconnectPath: '/api/v1/integrations/oauth/{slug}/disconnect',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/user') return { credentials: [] };
      if (path === '/api/v1/integrations') return [];
      if (path === '/api/v1/integrations/catalog') {
        return [
          {
            id: 'jira',
            slug: 'jira',
            name: 'Jira Cloud',
            description: 'Jira Cloud issue tracking',
            integration_type: 'issue_tracker',
            auth_type: 'api_key',
            credential_schema: {
              required: ['email', 'api_token'],
              properties: {
                email: { label: 'Atlassian account email', type: 'string' },
                api_token: { label: 'API token', type: 'password' },
              },
            },
            config_schema: {
              required: ['site_url'],
              properties: {
                site_url: { label: 'Jira site URL', type: 'url' },
                project_keys: { label: 'Allowed project keys', type: 'string[]' },
                labels: { label: 'Allowed issue labels', type: 'string[]' },
              },
            },
            credential_enrollment: {
              method: 'oauth_authorization_code',
              credential_field: 'access_token',
              default_credential_name: 'jira-signin',
            },
            sign_in_available: true,
            oauth_client_secret_required: true,
          },
        ];
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    wrap(<SettingsPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Set up Jira Cloud' }));
    expect(screen.getByRole('button', { name: /Sign in with Atlassian/ })).toBeTruthy();
    const tokenMode = screen.getByRole('button', { name: /Use an API token/ });
    expect(tokenMode).toBeTruthy();
    fireEvent.click(tokenMode);

    fireEvent.change(await screen.findByTestId('setup-input-jira-email'), {
      target: { value: 'ada@example.com' },
    });
    fireEvent.change(screen.getByTestId('setup-input-jira-api_token'), {
      target: { value: 'secret' },
    });
    fireEvent.change(screen.getByTestId('setup-config-jira-site_url'), {
      target: { value: 'https://example.atlassian.net' },
    });
    fireEvent.change(screen.getByTestId('setup-config-jira-project_keys'), {
      target: { value: 'PROJ, OPS' },
    });
    fireEvent.change(screen.getByTestId('setup-config-jira-labels'), {
      target: { value: 'auth, security' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Connect Jira Cloud' }));

    await waitFor(() =>
      expect(apiMocks.post).toHaveBeenCalledWith('/api/v1/integrations', {
        slug: 'jira',
        enabled: true,
        credential: {
          name: 'jira-setup',
          data: { email: 'ada@example.com', api_token: 'secret' },
        },
        config: {
          site_url: 'https://example.atlassian.net',
          project_keys: ['PROJ', 'OPS'],
          labels: ['auth', 'security'],
        },
      }),
    );
  });

  it.each(['codex', 'claude-code'])('connects %s through shared Integrations', async (slug) => {
    const isClaude = slug === 'claude-code';
    const label = isClaude ? 'Claude Code' : 'Codex';
    routerMocks.params = { providerId: 'integrations', sectionId: 'connections' };
    const challenge = {
      id: 'enrollment-1',
      connectionId: 'codex-connection-1',
      providerSlug: slug,
      credentialName: 'codex-credentials',
      state: 'awaiting_user',
      verificationUri: 'https://auth.openai.com/codex/device',
      userCode: 'ABCD-EFGH',
      expiresAt: '2026-08-01T12:15:00Z',
      errorCode: '',
      inputRequired: isClaude,
    };
    apiMocks.post.mockImplementation(async (path: string) => {
      if (path === '/api/v1/integrations/enrollments') return challenge;
      return null;
    });
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Integrations',
          scope: 'user',
          sections: [
            {
              id: 'connections',
              label: 'Connections',
              fields: [],
              resources: [
                {
                  id: 'integration_connections',
                  type: 'integrations',
                  label: 'Integration connections',
                  listPath: '/api/v1/integrations',
                  catalogPath: '/api/v1/integrations/catalog',
                  createPath: '/api/v1/integrations',
                  deletePath: '/api/v1/integrations/{id}',
                  credentialListPath: '/api/v1/credentials/user',
                  testPath: '/api/v1/integrations/{id}/test',
                  oauthAuthorizePath: '/api/v1/integrations/oauth/{slug}/authorize',
                  oauthDisconnectPath: '/api/v1/integrations/oauth/{slug}/disconnect',
                  enrollmentStartPath: '/api/v1/integrations/enrollments',
                  enrollmentStatusPath: '/api/v1/integrations/enrollments/{id}',
                  enrollmentCancelPath: '/api/v1/integrations/enrollments/{id}',
                  enrollmentCodePath: '/api/v1/integrations/enrollments/{id}/code',
                },
              ],
            },
          ],
        };
      }
      if (path === '/api/v1/credentials/user') return { credentials: [] };
      if (path === '/api/v1/integrations') {
        return [
          {
            id: 'codex-connection-1',
            slug,
            integrationType: 'ai_provider',
            credentialName: 'codex-credentials',
            credentialStatus: 'auth_required',
            enabled: true,
          },
        ];
      }
      if (path === '/api/v1/integrations/catalog') {
        return [
          {
            id: slug,
            slug,
            name: isClaude ? 'Claude Code (subscription)' : 'OpenAI Codex (ChatGPT)',
            integration_type: 'ai_provider',
            auth_type: isClaude ? 'browser_login' : 'device_code',
            credential_schema: {},
            config_schema: {},
            credential_enrollment: {
              method: isClaude ? 'claude_setup' : 'codex_device',
              credential_field: 'auth.json',
              default_credential_name: 'codex-credentials',
            },
          },
        ];
      }
      if (path === '/api/v1/integrations/enrollments/enrollment-1') return challenge;
      throw new Error(`Unexpected GET ${path}`);
    });

    wrap(<SettingsPage />);

    const reconnect = await screen.findByRole('button', { name: `Reconnect ${label}` });
    expect(screen.getByText('Reconnect required')).toBeTruthy();
    fireEvent.click(reconnect);

    if (isClaude) {
      fireEvent.change(await screen.findByLabelText('Authorization code'), {
        target: { value: 'test-browser-code' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Complete sign-in' }));
      await waitFor(() =>
        expect(apiMocks.post).toHaveBeenCalledWith(
          '/api/v1/integrations/enrollments/enrollment-1/code',
          { code: 'test-browser-code' },
        ),
      );
      expect(await screen.findByText('Completing sign-in…')).toBeTruthy();
    } else {
      expect(await screen.findByText('ABCD-EFGH')).toBeTruthy();
    }
    expect(screen.getByRole('link', { name: 'the provider login page' }).getAttribute('href')).toBe(
      challenge.verificationUri,
    );
    expect(apiMocks.post).toHaveBeenCalledWith('/api/v1/integrations/enrollments', {
      slug,
      credential_name: 'codex-credentials',
      connection_id: 'codex-connection-1',
    });
    expect(screen.queryByText('Create a new credential')).toBeNull();
  });

  it('renders the missing-provider state when a service base URL is not configured', async () => {
    routerMocks.params = { providerId: 'ting', sectionId: '' };

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    });

    render(
      <ConfigProvider
        value={{
          demoMode: false,
          theme: 'ice',
          plugins: {
            ting: { enabled: true, order: 2 },
          },
          services: {},
        }}
      >
        <QueryClientProvider client={queryClient}>
          <SettingsPage />
        </QueryClientProvider>
      </ConfigProvider>,
    );

    expect(await screen.findByRole('heading', { name: 'Ting Settings' })).toBeTruthy();
    expect(
      screen.getByText(
        'This service does not have a live settings endpoint configured in the current host profile.',
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        'No service base URL is configured for this provider in the active app profile.',
      ),
    ).toBeTruthy();
  });

  it('renders the schema error state when a mounted provider fails to load', async () => {
    routerMocks.params = { providerId: 'identity', sectionId: '' };
    apiMocks.get.mockRejectedValueOnce(new Error('boom'));

    wrap(<SettingsPage />);

    expect(await screen.findByRole('heading', { name: 'You Settings' })).toBeTruthy();
    expect(
      await screen.findByText(
        'This service responded, but its settings schema could not be loaded.',
      ),
    ).toBeTruthy();
    expect(await screen.findByText('Expected endpoint:')).toBeTruthy();
  });

  it('only loads the active remote provider schema', async () => {
    routerMocks.params = { providerId: 'ting', sectionId: 'general' };
    apiMocks.get.mockImplementation(async (path: string) => {
      if (path === '/settings') {
        return {
          title: 'Ting',
          subtitle: 'saga coordinator settings',
          scope: 'service',
          sections: [
            {
              id: 'general',
              label: 'General',
              description: 'Core service bindings for the coordinator.',
              fields: [
                {
                  key: 'service_name',
                  label: 'Service',
                  type: 'text',
                  value: 'Ting',
                  readOnly: true,
                },
              ],
            },
          ],
        };
      }
      throw new Error(`Unexpected GET ${path}`);
    });

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: {
          retry: false,
        },
      },
    });

    render(
      <ConfigProvider
        value={{
          demoMode: false,
          theme: 'ice',
          plugins: {
            ting: { enabled: true, order: 1 },
            ravn: { enabled: true, order: 2 },
          },
          services: {
            ting: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ting' },
            ravn: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/ravn' },
          },
        }}
      >
        <QueryClientProvider client={queryClient}>
          <SettingsPage />
        </QueryClientProvider>
      </ConfigProvider>,
    );

    expect(await screen.findByRole('heading', { name: 'General' })).toBeTruthy();
    expect(apiMocks.get.mock.calls.filter(([path]) => path === '/settings')).toHaveLength(1);
  });

  it.each([
    [401, 'This service rejected the current token while loading its settings schema.'],
    [403, 'You do not have permission to view this service settings surface.'],
    [404, 'This service is mounted, but it does not expose the expected settings route.'],
    [503, 'This service is configured, but it is not currently available.'],
  ])('renders the remote provider error copy for HTTP %i', async (status, expectedCopy) => {
    routerMocks.params = { providerId: 'ting', sectionId: '' };
    apiMocks.get.mockRejectedValueOnce({
      name: 'ApiClientError',
      message: `HTTP ${status}`,
      status,
      detail: 'upstream detail',
    });

    wrap(<SettingsPage />);

    expect(await screen.findByRole('heading', { name: 'Ting Settings' })).toBeTruthy();
    expect(await screen.findByText(expectedCopy)).toBeTruthy();
    expect(await screen.findByText('Response detail:')).toBeTruthy();
  });
});

it('renders locally mounted storage management inside the settings shell', async () => {
  routerMocks.params = { providerId: 'storage', sectionId: 'home' };
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ConfigProvider
      value={{
        demoMode: false,
        theme: 'ice',
        plugins: { volundr: { enabled: true, order: 1 } },
        services: {},
      }}
    >
      <QueryClientProvider client={queryClient}>
        <ServicesProvider services={{ volundr: { getTargets: async () => [] } }}>
          <SettingsPage />
        </ServicesProvider>
      </QueryClientProvider>
    </ConfigProvider>,
  );
  expect(await screen.findByRole('combobox', { name: 'Storage cluster' })).toBeTruthy();
  expect(await screen.findByText('No clusters are available to your account.')).toBeTruthy();
});
