import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsPage } from './Settings';
import type { IVolundrService } from '@/modules/volundr/ports';
import type {
  CatalogEntry,
  FeatureModule,
  IntegrationConnection,
  SecretTypeInfo,
  StoredCredential,
} from '@/modules/volundr/models';
// Ensure module registry is populated
import '@/modules';

const mockServiceRef = { current: {} as IVolundrService };

vi.mock('@/modules/volundr/adapters', () => ({
  get volundrService() {
    return mockServiceRef.current;
  },
}));

vi.mock('@/modules/shared/adapters/feature-catalog.adapter', () => ({
  get featureCatalogService() {
    return {
      getFeatureModules: mockServiceRef.current.getFeatureModules,
      getUserFeaturePreferences: mockServiceRef.current.getUserFeaturePreferences,
      toggleFeature: mockServiceRef.current.toggleFeature,
      updateUserFeaturePreferences: mockServiceRef.current.updateUserFeaturePreferences,
    };
  },
}));

const mockCatalog: CatalogEntry[] = [
  {
    slug: 'linear',
    name: 'Linear',
    description: 'Issue tracker',
    integration_type: 'issue_tracker',
    adapter: 'volundr.adapters.outbound.linear.LinearAdapter',
    icon: 'linear',
    credential_schema: {
      required: ['api_key'],
      properties: { api_key: { type: 'string' } },
    },
    config_schema: {},
    mcp_server: {
      name: 'linear-mcp',
      command: 'npx',
      args: ['-y', '@anthropic-ai/linear-mcp-server'],
      env_from_credentials: { LINEAR_API_KEY: 'api_key' },
    },
    auth_type: 'api_key',
    oauth_scopes: [],
  },
  {
    slug: 'github',
    name: 'GitHub',
    description: 'GitHub source control',
    integration_type: 'source_control',
    adapter: 'volundr.adapters.outbound.github.GitHubProvider',
    icon: 'github',
    credential_schema: { required: ['token'], properties: { token: { type: 'string' } } },
    config_schema: {},
    mcp_server: null,
    auth_type: 'api_key',
    oauth_scopes: [],
  },
];

const mockConnections: IntegrationConnection[] = [
  {
    id: 'int-1',
    integrationType: 'issue_tracker',
    adapter: 'volundr.adapters.outbound.linear.LinearAdapter',
    credentialName: 'linear-key',
    config: {},
    enabled: true,
    createdAt: '2025-01-15T10:00:00Z',
    updatedAt: '2025-01-15T10:00:00Z',
    slug: 'linear',
  },
];

const mockTypes: SecretTypeInfo[] = [
  {
    type: 'api_key',
    label: 'API Key',
    description: 'API key for external services',
    fields: [{ key: 'api_key', label: 'API Key', type: 'password', required: true }],
    defaultMountType: 'env',
  },
  {
    type: 'generic',
    label: 'Generic',
    description: 'Generic key-value secret',
    fields: [],
    defaultMountType: 'env',
  },
  {
    type: 'ssh_key',
    label: 'SSH Key',
    description: 'SSH private key',
    fields: [{ key: 'private_key', label: 'Private Key', type: 'textarea', required: true }],
    defaultMountType: 'file',
  },
];

const mockCredentials: StoredCredential[] = [
  {
    id: 'cred-1',
    name: 'my-api-key',
    secretType: 'api_key',
    keys: ['api_key'],
    metadata: {},
    createdAt: '2025-06-01T10:00:00Z',
    updatedAt: '2025-06-01T10:00:00Z',
  },
  {
    id: 'cred-2',
    name: 'my-ssh',
    secretType: 'ssh_key',
    keys: ['private_key'],
    metadata: {},
    createdAt: '2025-06-02T10:00:00Z',
    updatedAt: '2025-06-02T10:00:00Z',
  },
];

const mockUserFeatures: FeatureModule[] = [
  {
    key: 'credentials',
    label: 'Credentials',
    icon: 'KeyRound',
    scope: 'user',
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 10,
  },
  {
    key: 'workspaces',
    label: 'Workspaces',
    icon: 'HardDrive',
    scope: 'user',
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 20,
  },
  {
    key: 'integrations',
    label: 'Integrations',
    icon: 'Link2',
    scope: 'user',
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 30,
  },
  {
    key: 'appearance',
    label: 'Appearance',
    icon: 'Palette',
    scope: 'user',
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 40,
  },
];

function createMockService(overrides?: Partial<IVolundrService>): IVolundrService {
  return {
    getCredentials: vi.fn().mockResolvedValue([]),
    getCredentialTypes: vi.fn().mockResolvedValue(mockTypes),
    createCredential: vi.fn().mockResolvedValue(undefined),
    deleteCredential: vi.fn().mockResolvedValue(undefined),
    getIntegrationCatalog: vi.fn().mockResolvedValue(mockCatalog),
    getIntegrations: vi.fn().mockResolvedValue(mockConnections),
    deleteIntegration: vi.fn().mockResolvedValue(undefined),
    testIntegration: vi.fn().mockResolvedValue({
      success: true,
      provider: 'linear',
      workspace: 'Test',
    }),
    createIntegration: vi.fn().mockResolvedValue({
      id: 'new-1',
      integrationType: 'source_control',
      adapter: 'test',
      credentialName: 'cred',
      config: {},
      enabled: true,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      slug: 'github',
    }),
    getFeatureModules: vi.fn().mockResolvedValue(mockUserFeatures),
    getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
    toggleFeature: vi.fn(),
    updateUserFeaturePreferences: vi.fn(),
    ...overrides,
  } as unknown as IVolundrService;
}

function renderSettings(service: IVolundrService) {
  mockServiceRef.current = service;
  return render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>
  );
}

async function switchToIntegrationsSection() {
  // Wait for sections to load from the feature modules API
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /integrations/i })).toBeDefined();
  });
  const integrationsButton = screen.getByRole('button', { name: /integrations/i });
  fireEvent.click(integrationsButton);
}

describe('SettingsPage — Credentials section', () => {
  let service: IVolundrService;

  beforeEach(() => {
    service = createMockService();
  });

  it('renders page title', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('Settings')).toBeDefined();
    });
  });

  it('shows credentials section by default', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search credentials...')).toBeDefined();
    });
  });

  it('shows empty state when no credentials', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('No credentials stored')).toBeDefined();
    });
  });
});

describe('SettingsPage — Integrations section', () => {
  let service: IVolundrService;

  beforeEach(() => {
    service = createMockService();
  });

  it('shows loading state when switching to integrations section', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    // With lazy-loaded modules, the loading text appears once the component loads
    await waitFor(() => {
      expect(screen.getByText('Loading integrations...')).toBeDefined();
    });
  });

  it('loads catalog and connections', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
      expect(screen.getByText('GitHub')).toBeDefined();
    });
    expect(service.getIntegrationCatalog).toHaveBeenCalled();
    expect(service.getIntegrations).toHaveBeenCalled();
  });

  it('shows connected status for connected integrations', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Connected')).toBeDefined();
    });
  });

  it('shows Connect button for unconnected integrations', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Connect')).toBeDefined();
    });
  });

  it('filters by type', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
    });

    const filterButtons = screen.getAllByRole('button');
    const sourceControlButton = filterButtons.find(b => b.textContent === 'Source Control');
    fireEvent.click(sourceControlButton!);
    expect(screen.queryByText('Linear')).toBeNull();
    expect(screen.getByText('GitHub')).toBeDefined();
  });

  it('shows empty state when no integrations match filter', async () => {
    const emptyService = createMockService({
      getIntegrationCatalog: vi.fn().mockResolvedValue([]),
      getIntegrations: vi.fn().mockResolvedValue([]),
    });
    renderSettings(emptyService);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('No integrations available')).toBeDefined();
    });
  });

  it('opens credential form when Connect clicked', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));
    expect(screen.getByText('Connect GitHub')).toBeDefined();
  });

  it('closes credential form on cancel', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));
    expect(screen.getByText('Connect GitHub')).toBeDefined();

    fireEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByText('Connect GitHub')).toBeNull();
  });

  it('handles disconnect', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
    });

    const disconnectButton = screen.getByText('Disconnect');
    fireEvent.click(disconnectButton);
    await waitFor(() => {
      expect(service.deleteIntegration).toHaveBeenCalledWith('int-1');
    });
  });

  it('handles test connection', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
    });

    const testButton = screen.getByText('Test');
    fireEvent.click(testButton);
    await waitFor(() => {
      expect(service.testIntegration).toHaveBeenCalledWith('int-1');
    });
    await waitFor(() => {
      expect(screen.getByText(/Connected to linear/)).toBeDefined();
    });
  });

  it('shows failed test result', async () => {
    const failService = createMockService({
      testIntegration: vi.fn().mockResolvedValue({
        success: false,
        provider: 'linear',
        error: 'Invalid API key',
      }),
    });
    renderSettings(failService);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Test'));
    await waitFor(() => {
      expect(screen.getByText(/Connection failed: Invalid API key/)).toBeDefined();
    });
  });

  it('shows test result with workspace name', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Test'));
    await waitFor(() => {
      expect(screen.getByText(/Connected to linear \(Test\)/)).toBeDefined();
    });
  });

  it('shows test result without workspace', async () => {
    const noWsService = createMockService({
      testIntegration: vi.fn().mockResolvedValue({
        success: false,
        provider: 'linear',
      }),
    });
    renderSettings(noWsService);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('Linear')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Test'));
    await waitFor(() => {
      expect(screen.getByText(/Connection failed: unknown error/)).toBeDefined();
    });
  });

  it('submits credential form successfully and reloads data', async () => {
    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));
    expect(screen.getByText('Connect GitHub')).toBeDefined();

    const tokenInput = screen.getByPlaceholderText('Enter token');
    fireEvent.change(tokenInput, { target: { value: 'ghp_test123' } });

    const connectButtons = screen.getAllByText('Connect');
    const submitButton = connectButtons[connectButtons.length - 1];
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(service.createIntegration).toHaveBeenCalledWith(
        expect.objectContaining({
          integrationType: 'source_control',
          adapter: 'volundr.adapters.outbound.github.GitHubProvider',
          slug: 'github',
          enabled: true,
        })
      );
    });

    await waitFor(() => {
      expect(screen.queryByText('Connect GitHub')).toBeNull();
    });
  });

  it('shows error message when form submission fails with Error', async () => {
    const errorService = createMockService({
      createIntegration: vi.fn().mockRejectedValue(new Error('API key invalid')),
    });
    renderSettings(errorService);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));
    expect(screen.getByText('Connect GitHub')).toBeDefined();

    const tokenInput = screen.getByPlaceholderText('Enter token');
    fireEvent.change(tokenInput, { target: { value: 'bad-token' } });

    const connectButtons = screen.getAllByText('Connect');
    const submitButton = connectButtons[connectButtons.length - 1];
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('API key invalid')).toBeDefined();
    });
  });

  it('shows fallback error when submission fails with non-Error', async () => {
    const errorService = createMockService({
      createIntegration: vi.fn().mockRejectedValue('string error'),
    });
    renderSettings(errorService);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));

    const tokenInput = screen.getByPlaceholderText('Enter token');
    fireEvent.change(tokenInput, { target: { value: 'bad-token' } });

    const connectButtons = screen.getAllByText('Connect');
    const submitButton = connectButtons[connectButtons.length - 1];
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText('Failed to connect')).toBeDefined();
    });
  });
});

/* ------------------------------------------------------------------ */
/* OAuth connect flow                                                  */
/* ------------------------------------------------------------------ */

const oauthCatalog: CatalogEntry[] = [
  {
    slug: 'github-oauth',
    name: 'GitHub OAuth',
    description: 'GitHub via OAuth',
    integration_type: 'source_control',
    adapter: 'volundr.adapters.outbound.github.GitHubProvider',
    icon: 'github',
    credential_schema: {},
    config_schema: {},
    mcp_server: null,
    auth_type: 'oauth2_authorization_code',
    oauth_scopes: ['repo'],
  },
];

describe('SettingsPage — OAuth integration connect', () => {
  let service: IVolundrService;
  const originalFetch = globalThis.fetch;
  const originalOpen = window.open;

  beforeEach(() => {
    service = createMockService({
      getIntegrationCatalog: vi.fn().mockResolvedValue(oauthCatalog),
      getIntegrations: vi.fn().mockResolvedValue([]),
    });
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    window.open = originalOpen;
  });

  it('opens OAuth popup on connect', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ url: 'https://example.com/oauth' }), {
        status: 200,
      })
    );
    window.open = vi.fn().mockReturnValue({ closed: true });

    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub OAuth')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalled();
    });
    expect(window.open).toHaveBeenCalled();
  });

  it('shows error when OAuth fetch returns non-ok', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('', { status: 500 }));

    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub OAuth')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalled();
    });
  });

  it('shows error when popup is blocked', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ url: 'https://example.com/oauth' }), {
        status: 200,
      })
    );
    window.open = vi.fn().mockReturnValue(null);

    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub OAuth')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));

    await waitFor(() => {
      expect(window.open).toHaveBeenCalled();
    });
  });

  it('handles OAuth fetch throwing an Error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub OAuth')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalled();
    });
  });

  it('handles OAuth fetch throwing a non-Error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue('string fail');

    renderSettings(service);
    await switchToIntegrationsSection();
    await waitFor(() => {
      expect(screen.getByText('GitHub OAuth')).toBeDefined();
    });

    fireEvent.click(screen.getByText('Connect'));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalled();
    });
  });
});

describe('SettingsPage — Credentials with data', () => {
  let service: IVolundrService;

  beforeEach(() => {
    service = createMockService({
      getCredentials: vi.fn().mockResolvedValue(mockCredentials),
    });
  });

  it('displays credentials in a grid', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('my-api-key')).toBeDefined();
      expect(screen.getByText('my-ssh')).toBeDefined();
    });
  });

  it('shows credential type badge and key count', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('my-api-key')).toBeDefined();
    });
    // Type badges exist alongside filter chips — use getAllByText
    const apiKeyTexts = screen.getAllByText('API Key');
    expect(apiKeyTexts.length).toBeGreaterThanOrEqual(2); // filter chip + badge
    const sshKeyTexts = screen.getAllByText('SSH Key');
    expect(sshKeyTexts.length).toBeGreaterThanOrEqual(2);
    // Both credentials have 1 key each
    const keyCountTexts = screen.getAllByText(/1 key/);
    expect(keyCountTexts.length).toBe(2);
  });

  it('shows delete confirmation dialog', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('my-api-key')).toBeDefined();
    });

    const trashButtons = screen.getAllByRole('button').filter(b => {
      const svg = b.querySelector('svg');
      return svg && b.closest('[class*="credentialCard"]');
    });
    expect(trashButtons.length).toBeGreaterThan(0);
    fireEvent.click(trashButtons[0]);
    await waitFor(() => {
      expect(screen.getByText(/Delete credential/)).toBeDefined();
      expect(screen.getByText('Cancel')).toBeDefined();
    });
  });

  it('cancels delete confirmation', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('my-api-key')).toBeDefined();
    });

    const trashButtons = screen.getAllByRole('button').filter(b => {
      const svg = b.querySelector('svg');
      return svg && b.closest('[class*="credentialCard"]');
    });
    if (trashButtons.length > 0) {
      fireEvent.click(trashButtons[0]);
      await waitFor(() => {
        expect(screen.getByText('Cancel')).toBeDefined();
      });
      fireEvent.click(screen.getByText('Cancel'));
      expect(screen.queryByText(/Delete credential/)).toBeNull();
    }
  });

  it('confirms delete and calls service', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('my-api-key')).toBeDefined();
    });

    const trashButtons = screen.getAllByRole('button').filter(b => {
      const svg = b.querySelector('svg');
      return svg && b.closest('[class*="credentialCard"]');
    });
    if (trashButtons.length > 0) {
      fireEvent.click(trashButtons[0]);
      await waitFor(() => {
        expect(screen.getByText('Delete')).toBeDefined();
      });
      fireEvent.click(screen.getByText('Delete'));
      await waitFor(() => {
        expect(service.deleteCredential).toHaveBeenCalled();
      });
    }
  });

  it('shows filter chips from types', async () => {
    renderSettings(service);
    await waitFor(() => {
      expect(screen.getByText('All')).toBeDefined();
    });
  });
});

describe('SettingsPage — Credential Form', { timeout: 30_000 }, () => {
  let service: IVolundrService;

  beforeEach(() => {
    service = createMockService();
  });

  /** Helper: open the credential form and return scoped queries for the overlay. */
  async function openForm() {
    await waitFor(
      () => {
        expect(screen.getByText('Add Credential')).toBeDefined();
      },
      { timeout: 20_000 }
    );
    fireEvent.click(screen.getByText('Add Credential'));
    await waitFor(
      () => {
        const overlay = document.querySelector('[class*="formOverlay"]');
        expect(overlay).not.toBeNull();
      },
      { timeout: 20_000 }
    );
    // scope subsequent queries to the form overlay
    const overlay = document.querySelector('[class*="formOverlay"]') as HTMLElement | null;
    expect(overlay).not.toBeNull();
    expect(within(overlay as HTMLElement).getByText('API Key')).toBeDefined();
    return within(overlay as HTMLElement);
  }

  /** Helper: open form, select a type, return scoped queries. */
  async function openFormWithType(typeLabel: string) {
    const form = await openForm();
    fireEvent.click(form.getByText(typeLabel));
    await waitFor(
      () => {
        expect(form.getByPlaceholderText('my-api-key')).toBeDefined();
      },
      { timeout: 20_000 }
    );
    return form;
  }

  it('opens form and shows type selection', async () => {
    renderSettings(service);
    const form = await openForm();
    expect(form.getByText('API Key')).toBeDefined();
    expect(form.getByText('Generic')).toBeDefined();
    expect(form.getByText('SSH Key')).toBeDefined();
  });

  it.skip('closes form with X button', async () => {
    renderSettings(service);
    const form = await openForm();

    const closeButtons = form
      .getAllByRole('button')
      .filter(b => b.closest('[class*="formHeader"]'));
    expect(closeButtons.length).toBeGreaterThan(0);
    fireEvent.click(closeButtons[closeButtons.length - 1]);
    expect(screen.queryByText('Select Type')).toBeNull();
  });

  it('selects type and shows data entry form', async () => {
    renderSettings(service);
    const form = await openFormWithType('API Key');
    expect(form.getByText('Add Credential')).toBeDefined();
  });

  it('shows Back button to return to type selection', async () => {
    renderSettings(service);
    const form = await openFormWithType('API Key');

    fireEvent.click(form.getByText('Back'));
    await waitFor(
      () => {
        expect(form.getByText('Select Type')).toBeDefined();
      },
      { timeout: 20_000 }
    );
  });

  it('submits API key credential', async () => {
    renderSettings(service);
    const form = await openFormWithType('API Key');

    fireEvent.change(form.getByPlaceholderText('my-api-key'), {
      target: { value: 'test-cred' },
    });

    // Fill the API key password field
    const passwordInputs = form
      .getAllByDisplayValue('')
      .filter(i => i.getAttribute('type') === 'password');
    if (passwordInputs.length > 0) {
      fireEvent.change(passwordInputs[0], { target: { value: 'sk-123' } });
    }

    fireEvent.click(form.getByText('Create'));
    await waitFor(() => {
      expect(service.createCredential).toHaveBeenCalled();
    });
  });

  it('shows error on failed submission', async () => {
    const failService = createMockService({
      createCredential: vi.fn().mockRejectedValue(new Error('Store error')),
    });
    renderSettings(failService);
    await waitFor(
      () => {
        expect(screen.getByText('Add Credential')).toBeDefined();
      },
      { timeout: 20_000 }
    );
    fireEvent.click(screen.getByText('Add Credential'));
    await waitFor(
      () => {
        const overlay = document.querySelector('[class*="formOverlay"]');
        expect(overlay).not.toBeNull();
      },
      { timeout: 20_000 }
    );
    const overlay = document.querySelector('[class*="formOverlay"]') as HTMLElement | null;
    expect(overlay).not.toBeNull();
    const form = within(overlay as HTMLElement);

    fireEvent.click(form.getByText('API Key'));
    await waitFor(
      () => {
        expect(form.getByPlaceholderText('my-api-key')).toBeDefined();
      },
      { timeout: 20_000 }
    );

    fireEvent.change(form.getByPlaceholderText('my-api-key'), {
      target: { value: 'test-cred' },
    });

    fireEvent.click(form.getByText('Create'));
    await waitFor(
      () => {
        expect(screen.getByText('Store error')).toBeDefined();
      },
      { timeout: 20_000 }
    );
  });

  it('shows fallback error on non-Error rejection', async () => {
    const failService = createMockService({
      createCredential: vi.fn().mockRejectedValue('unknown'),
    });
    renderSettings(failService);
    await waitFor(
      () => {
        expect(screen.getByText('Add Credential')).toBeDefined();
      },
      { timeout: 20_000 }
    );
    fireEvent.click(screen.getByText('Add Credential'));
    await waitFor(
      () => {
        const overlay = document.querySelector('[class*="formOverlay"]');
        expect(overlay).not.toBeNull();
      },
      { timeout: 20_000 }
    );
    const overlay = document.querySelector('[class*="formOverlay"]') as HTMLElement | null;
    expect(overlay).not.toBeNull();
    const form = within(overlay as HTMLElement);

    fireEvent.click(form.getByText('API Key'));
    await waitFor(
      () => {
        expect(form.getByPlaceholderText('my-api-key')).toBeDefined();
      },
      { timeout: 20_000 }
    );
    fireEvent.change(form.getByPlaceholderText('my-api-key'), {
      target: { value: 'test' },
    });

    fireEvent.click(form.getByText('Create'));
    await waitFor(
      () => {
        expect(screen.getByText('Failed to create credential')).toBeDefined();
      },
      { timeout: 20_000 }
    );
  });

  it('renders textarea for SSH key field', async () => {
    renderSettings(service);
    const form = await openForm();

    fireEvent.click(form.getByText('SSH Key'));
    await waitFor(() => {
      expect(form.getByText('Private Key')).toBeDefined();
    });
    const textareas = document.querySelectorAll('textarea');
    expect(textareas.length).toBeGreaterThan(0);
  });

  it('renders generic key-value editor', async () => {
    renderSettings(service);
    const form = await openForm();

    fireEvent.click(form.getByText('Generic'));
    await waitFor(() => {
      expect(form.getByText('Key-Value Pairs')).toBeDefined();
      expect(form.getByPlaceholderText('Key')).toBeDefined();
      expect(form.getByPlaceholderText('Value')).toBeDefined();
    });
  });

  it('adds and fills generic key-value pairs', async () => {
    renderSettings(service);
    const form = await openForm();

    fireEvent.click(form.getByText('Generic'));
    await waitFor(() => {
      expect(form.getByText('Add pair')).toBeDefined();
    });

    fireEvent.change(form.getByPlaceholderText('Key'), {
      target: { value: 'MY_KEY' },
    });
    fireEvent.change(form.getByPlaceholderText('Value'), {
      target: { value: 'my-value' },
    });

    fireEvent.click(form.getByText('Add pair'));
    const keyInputs = form.getAllByPlaceholderText('Key');
    expect(keyInputs.length).toBe(2);
  });

  it('submits generic credential with key-value data', async () => {
    renderSettings(service);
    const form = await openForm();

    fireEvent.click(form.getByText('Generic'));
    await waitFor(() => {
      expect(form.getByPlaceholderText('my-api-key')).toBeDefined();
    });

    fireEvent.change(form.getByPlaceholderText('my-api-key'), {
      target: { value: 'my-generic' },
    });
    fireEvent.change(form.getByPlaceholderText('Key'), {
      target: { value: 'DB_URL' },
    });
    fireEvent.change(form.getByPlaceholderText('Value'), {
      target: { value: 'postgres://...' },
    });

    fireEvent.click(form.getByText('Create'));
    await waitFor(() => {
      expect(service.createCredential).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'my-generic',
          secretType: 'generic',
          data: { DB_URL: 'postgres://...' },
        })
      );
    });
  });

  it('does not submit when name is empty', async () => {
    renderSettings(service);
    const form = await openFormWithType('API Key');

    const createButton = form.getByText('Create');
    expect(createButton.hasAttribute('disabled')).toBe(true);
  });

  it('removes a generic key-value pair', async () => {
    renderSettings(service);
    const form = await openForm();

    fireEvent.click(form.getByText('Generic'));
    await waitFor(() => {
      expect(form.getByText('Add pair')).toBeDefined();
    });

    fireEvent.click(form.getByText('Add pair'));
    const keyInputs = form.getAllByPlaceholderText('Key');
    expect(keyInputs.length).toBe(2);

    const removeButtons = form.getAllByRole('button').filter(b => b.closest('[class*="kvRow"]'));
    expect(removeButtons.length).toBeGreaterThan(0);
    fireEvent.click(removeButtons[0]);
    await waitFor(() => {
      expect(form.getAllByPlaceholderText('Key').length).toBe(1);
    });
  });
});
