import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(async () => null),
  post: vi.fn(async (path: string) => {
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
          {
            id: 'integrations',
            label: 'Integrations',
            description: 'Connect services.',
            fields: [],
            resources: [
              {
                id: 'service_integrations',
                type: 'integrations',
                label: 'Service integrations',
                description: 'Create credentials and connect services.',
                listPath: '/api/v1/integrations',
                catalogPath: '/api/v1/integrations/catalog',
                createPath: '/api/v1/integrations',
                deletePath: '/api/v1/integrations/{id}',
                credentialCreatePath: '/api/v1/credentials/user',
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

function wrap(children: ReactNode) {
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
        theme: 'ice',
        plugins: {
          login: { enabled: true, order: 1 },
          ting: { enabled: true, order: 2 },
        },
        services: {
          identity: { mode: 'http', baseUrl: 'http://localhost:8080/api/v1/identity' },
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
    installDefaultGetMock();
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

  it('renders the integrations resource composer inside the unified shell', async () => {
    routerMocks.params = { providerId: 'ting', sectionId: 'integrations' };
    wrap(<SettingsPage />);

    expect(await screen.findByRole('heading', { name: 'Integrations' })).toBeTruthy();
    expect(screen.getByText('Service integrations')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add integration' })).toBeTruthy();
    expect(await screen.findByText('telegram')).toBeTruthy();
    expect(screen.getByRole('combobox')).toBeTruthy();
    expect(screen.getByDisplayValue('telegram-credential')).toBeTruthy();
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
});
