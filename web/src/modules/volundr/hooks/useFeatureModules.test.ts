import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useFeatureModules } from './useFeatureModules';
import type { IVolundrService } from '@/modules/volundr/ports';
import type { FeatureModule, UserFeaturePreference } from '@/modules/volundr/models';
import { registerModule } from '@/modules/registry';
import { Settings } from 'lucide-react';

// Register test modules in the registry
registerModule({
  key: 'credentials',
  load: () =>
    Promise.resolve({
      default: (() => null) as unknown as React.ComponentType<{ service: IVolundrService }>,
    }),
  icon: Settings,
});

registerModule({
  key: 'workspaces',
  load: () =>
    Promise.resolve({
      default: (() => null) as unknown as React.ComponentType<{ service: IVolundrService }>,
    }),
  icon: Settings,
});

const mockFeatures: FeatureModule[] = [
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
];

const mockPreferences: UserFeaturePreference[] = [];

function createMockService(overrides: Partial<IVolundrService> = {}): IVolundrService {
  return {
    getFeatureModules: vi.fn().mockResolvedValue(mockFeatures),
    getUserFeaturePreferences: vi.fn().mockResolvedValue(mockPreferences),
    toggleFeature: vi.fn(),
    updateUserFeaturePreferences: vi.fn(),
    // Stubs
    getSessions: vi.fn(),
    getSession: vi.fn(),
    getActiveSessions: vi.fn(),
    getStats: vi.fn(),
    getModels: vi.fn(),
    getRepos: vi.fn(),
    getFeatures: vi.fn(),
    subscribe: vi.fn(() => vi.fn()),
    subscribeStats: vi.fn(() => vi.fn()),
    startSession: vi.fn(),
    connectSession: vi.fn(),
    stopSession: vi.fn(),
    resumeSession: vi.fn(),
    deleteSession: vi.fn(),
    archiveSession: vi.fn(),
    restoreSession: vi.fn(),
    listArchivedSessions: vi.fn(),
    getMessages: vi.fn(),
    sendMessage: vi.fn(),
    subscribeMessages: vi.fn(() => vi.fn()),
    getLogs: vi.fn(),
    subscribeLogs: vi.fn(() => vi.fn()),
    getCodeServerUrl: vi.fn(),
    getChronicle: vi.fn(),
    subscribeChronicle: vi.fn(() => vi.fn()),
    getSessionDiff: vi.fn(),
    getPullRequests: vi.fn(),
    createPullRequest: vi.fn(),
    mergePullRequest: vi.fn(),
    getCIStatus: vi.fn(),
    getSessionMcpServers: vi.fn(),
    getAvailableMcpServers: vi.fn(),
    getAvailableSecrets: vi.fn(),
    createSecret: vi.fn(),
    searchLinearIssues: vi.fn(),
    getProjectRepoMappings: vi.fn(),
    getSessionFiles: vi.fn(),
    updateLinearIssueStatus: vi.fn(),
    getTemplates: vi.fn(),
    getTemplate: vi.fn(),
    saveTemplate: vi.fn(),
    getPresets: vi.fn(),
    getPreset: vi.fn(),
    savePreset: vi.fn(),
    deletePreset: vi.fn(),
    getIdentity: vi.fn(),
    getTenants: vi.fn(),
    getTenant: vi.fn(),
    getCredentials: vi.fn(),
    getCredentialTypes: vi.fn(),
    createCredential: vi.fn(),
    deleteCredential: vi.fn(),
    getUserCredentials: vi.fn(),
    storeUserCredential: vi.fn(),
    deleteUserCredential: vi.fn(),
    getTenantCredentials: vi.fn(),
    storeTenantCredential: vi.fn(),
    deleteTenantCredential: vi.fn(),
    getCredential: vi.fn(),
    getAdminSettings: vi.fn(),
    updateAdminSettings: vi.fn(),
    ...overrides,
  } as unknown as IVolundrService;
}

describe('useFeatureModules', () => {
  let service: IVolundrService;

  beforeEach(() => {
    vi.clearAllMocks();
    service = createMockService();
  });

  it('fetches features and preferences on mount', async () => {
    const { result } = renderHook(() => useFeatureModules('user', service));

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.features).toEqual(mockFeatures);
    expect(result.current.error).toBeNull();
  });

  it('builds sections from features and registry', async () => {
    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.sections).toHaveLength(2);
    expect(result.current.sections[0].key).toBe('credentials');
    expect(result.current.sections[1].key).toBe('workspaces');
    expect(result.current.sections[0].label).toBe('Credentials');
  });

  it('filters out disabled features', async () => {
    const featuresWithDisabled: FeatureModule[] = [
      { ...mockFeatures[0] },
      { ...mockFeatures[1], enabled: false },
    ];

    service = createMockService({
      getFeatureModules: vi.fn().mockResolvedValue(featuresWithDisabled),
    });

    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.sections).toHaveLength(1);
    expect(result.current.sections[0].key).toBe('credentials');
  });

  it('respects user preference visibility', async () => {
    const prefs: UserFeaturePreference[] = [
      { featureKey: 'credentials', visible: false, sortOrder: 0 },
    ];

    service = createMockService({
      getUserFeaturePreferences: vi.fn().mockResolvedValue(prefs),
    });

    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.sections).toHaveLength(1);
    expect(result.current.sections[0].key).toBe('workspaces');
  });

  it('respects user preference sort order', async () => {
    const prefs: UserFeaturePreference[] = [
      { featureKey: 'workspaces', visible: true, sortOrder: 5 },
      { featureKey: 'credentials', visible: true, sortOrder: 15 },
    ];

    service = createMockService({
      getUserFeaturePreferences: vi.fn().mockResolvedValue(prefs),
    });

    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.sections[0].key).toBe('workspaces');
    expect(result.current.sections[1].key).toBe('credentials');
  });

  it('handles fetch error', async () => {
    service = createMockService({
      getFeatureModules: vi.fn().mockRejectedValue(new Error('Network error')),
    });

    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('Network error');
    expect(result.current.sections).toHaveLength(0);
  });

  it('passes scope to service', async () => {
    const { result } = renderHook(() => useFeatureModules('admin', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(service.getFeatureModules).toHaveBeenCalledWith('admin');
  });

  it('handles non-Error thrown during fetch', async () => {
    service = createMockService({
      getFeatureModules: vi.fn().mockRejectedValue('string error'),
    });

    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('Failed to load features');
  });

  it('skips features not in module registry', async () => {
    const featuresWithUnknown: FeatureModule[] = [
      { ...mockFeatures[0] },
      {
        key: 'unknown-module',
        label: 'Unknown',
        icon: 'Settings',
        scope: 'user',
        enabled: true,
        defaultEnabled: true,
        adminOnly: false,
        order: 5,
      },
    ];

    service = createMockService({
      getFeatureModules: vi.fn().mockResolvedValue(featuresWithUnknown),
    });

    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    // Only credentials should appear; unknown-module is not in the registry
    expect(result.current.sections).toHaveLength(1);
    expect(result.current.sections[0].key).toBe('credentials');
  });

  it('refetch re-fetches features and preferences', async () => {
    const { result } = renderHook(() => useFeatureModules('user', service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      result.current.refetch();
    });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    // Called twice: once on mount, once on refetch
    expect(service.getFeatureModules).toHaveBeenCalledTimes(2);
  });
});
