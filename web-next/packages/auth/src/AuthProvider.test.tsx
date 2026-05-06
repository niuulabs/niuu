import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfigProvider } from '@niuulabs/plugin-sdk';
import type { NiuuConfig } from '@niuulabs/plugin-sdk';
import { AuthProvider } from './AuthProvider';
import { useAuth } from './useAuth';

// Mock the oidc module
vi.mock('./oidc', () => ({
  getOidcConfig: vi.fn(() => null),
  getUserManager: vi.fn(),
}));

// Mock the query token provider
vi.mock('@niuulabs/query', () => ({
  setTokenProvider: vi.fn(),
}));

import { getOidcConfig, getUserManager } from './oidc';
import { setTokenProvider } from '@niuulabs/query';

const baseConfig: NiuuConfig = {
  theme: 'ice',
  plugins: {},
  services: {},
};

const oidcConfig = {
  authority: 'https://auth.example.com',
  clientId: 'niuu-web',
  redirectUri: 'http://localhost:5173',
  postLogoutRedirectUri: 'http://localhost:5173',
  scope: 'openid profile email',
};

function createMockManager(overrides: Record<string, unknown> = {}) {
  return {
    getUser: vi.fn().mockResolvedValue(null),
    signinRedirect: vi.fn(),
    signinRedirectCallback: vi.fn(),
    signoutRedirect: vi.fn(),
    signinSilent: vi.fn(),
    events: {
      addUserLoaded: vi.fn(),
      addUserUnloaded: vi.fn(),
      addAccessTokenExpired: vi.fn(),
      removeUserLoaded: vi.fn(),
      removeUserUnloaded: vi.fn(),
      removeAccessTokenExpired: vi.fn(),
    },
    settings: { authority: oidcConfig.authority },
    ...overrides,
  };
}

function AuthStatus() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="enabled">{String(auth.enabled)}</span>
      <span data-testid="authenticated">{String(auth.authenticated)}</span>
      <span data-testid="loading">{String(auth.loading)}</span>
      <span data-testid="token">{auth.accessToken ?? 'none'}</span>
      <button data-testid="login" onClick={auth.login}>
        Sign in
      </button>
      <button data-testid="logout" onClick={auth.logout}>
        Logout
      </button>
    </div>
  );
}

function renderWithConfig(config: NiuuConfig = baseConfig) {
  return render(
    <ConfigProvider value={config}>
      <AuthProvider>
        <AuthStatus />
      </AuthProvider>
    </ConfigProvider>,
  );
}

describe('AuthProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getOidcConfig).mockReturnValue(null);
    Object.defineProperty(window, 'location', {
      value: {
        ...window.location,
        search: '',
        pathname: '/',
        origin: 'http://localhost:5173',
      },
      writable: true,
    });
  });

  it('renders children directly when OIDC is not configured', async () => {
    renderWithConfig();

    const enabled = await screen.findByTestId('enabled');
    expect(enabled).toHaveTextContent('false');
    expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    expect(screen.getByTestId('loading')).toHaveTextContent('false');
  });

  it('does not set token provider when auth is disabled', async () => {
    renderWithConfig();

    await screen.findByTestId('enabled');
    expect(setTokenProvider).not.toHaveBeenCalled();
  });

  it('renders children when OIDC is configured and user has existing session', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockUser = { expired: false, access_token: 'test-token' };
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(mockUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig({
      ...baseConfig,
      auth: { issuer: oidcConfig.authority, clientId: oidcConfig.clientId },
    });

    const enabled = await screen.findByTestId('enabled');
    expect(enabled).toHaveTextContent('true');
    expect(screen.getByTestId('authenticated')).toHaveTextContent('true');
    expect(screen.getByTestId('token')).toHaveTextContent('test-token');
  });

  it('shows loading spinner when OIDC is configured and session is loading', () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    // Use a pending promise so state never updates during this synchronous test
    const mockMgr = createMockManager({
      getUser: vi.fn().mockImplementation(() => new Promise(() => {})),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    const { container } = renderWithConfig();

    // While loading, the spinner div is rendered (no AuthStatus child)
    expect(container.querySelector('[class*="loadingPage"]')).not.toBeNull();
    expect(screen.queryByTestId('enabled')).toBeNull();
  });

  it('shows loading then children after unauthenticated session resolves', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(null),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    // After loading resolves, auth is enabled but not authenticated
    // Children rendered (RequireAuth would handle the redirect in real usage)
    const enabled = await screen.findByTestId('enabled');
    expect(enabled).toHaveTextContent('true');
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
  });

  it('shows loading when session is expired', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockUser = { expired: true, access_token: 'expired-token' };
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(mockUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    const enabled = await screen.findByTestId('enabled');
    expect(enabled).toHaveTextContent('true');
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');
  });

  it('handles OIDC callback with code in URL', async () => {
    Object.defineProperty(window, 'location', {
      value: {
        search: '?code=abc123&state=xyz',
        pathname: '/',
        origin: 'http://localhost:5173',
      },
      writable: true,
    });
    window.history.replaceState = vi.fn();

    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const callbackUser = { expired: false, access_token: 'callback-token' };
    const mockMgr = createMockManager({
      signinRedirectCallback: vi.fn().mockResolvedValue(callbackUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    const token = await screen.findByTestId('token');
    expect(token).toHaveTextContent('callback-token');
    expect(mockMgr.signinRedirectCallback).toHaveBeenCalled();
    expect(window.history.replaceState).toHaveBeenCalled();
  });

  it('calls signinRedirect when login is invoked', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockUser = { expired: false, access_token: 'test-token' };
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(mockUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    const user = userEvent.setup();
    await screen.findByTestId('login');
    await user.click(screen.getByTestId('login'));

    expect(mockMgr.signinRedirect).toHaveBeenCalled();
  });

  it('calls signoutRedirect when logout is invoked', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockUser = { expired: false, access_token: 'test-token' };
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(mockUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    const user = userEvent.setup();
    const logoutBtn = await screen.findByTestId('logout');
    await user.click(logoutBtn);

    expect(mockMgr.signoutRedirect).toHaveBeenCalled();
  });

  it('sets token provider when auth is enabled', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockUser = { expired: false, access_token: 'test-token' };
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(mockUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    await screen.findByTestId('authenticated');
    expect(setTokenProvider).toHaveBeenCalledWith(expect.any(Function));
  });

  it('handles error during OIDC init gracefully', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockMgr = createMockManager({
      getUser: vi.fn().mockRejectedValue(new Error('OIDC init error')),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    renderWithConfig();

    // Loading resolves even after error — children are shown (unauthenticated)
    const enabled = await screen.findByTestId('enabled');
    expect(enabled).toHaveTextContent('true');
    expect(screen.getByTestId('authenticated')).toHaveTextContent('false');

    consoleSpy.mockRestore();
  });

  it('handles userLoaded event from manager', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    let userLoadedCallback: ((u: unknown) => void) | undefined;
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(null),
      events: {
        addUserLoaded: vi.fn((cb: (u: unknown) => void) => {
          userLoadedCallback = cb;
        }),
        addUserUnloaded: vi.fn(),
        addAccessTokenExpired: vi.fn(),
        removeUserLoaded: vi.fn(),
        removeUserUnloaded: vi.fn(),
        removeAccessTokenExpired: vi.fn(),
      },
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    await screen.findByTestId('enabled');

    await act(async () => {
      userLoadedCallback?.({ expired: false, access_token: 'renewed-token' });
    });

    expect(screen.getByTestId('token')).toHaveTextContent('renewed-token');
  });

  it('calls signinSilent when access token expires', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    let tokenExpiredCallback: (() => void) | undefined;
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(null),
      signinSilent: vi.fn().mockResolvedValue({ expired: false, access_token: 'renewed' }),
      events: {
        addUserLoaded: vi.fn(),
        addUserUnloaded: vi.fn(),
        addAccessTokenExpired: vi.fn((cb: () => void) => {
          tokenExpiredCallback = cb;
        }),
        removeUserLoaded: vi.fn(),
        removeUserUnloaded: vi.fn(),
        removeAccessTokenExpired: vi.fn(),
      },
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    await screen.findByTestId('enabled');

    await act(async () => {
      tokenExpiredCallback?.();
    });

    expect(mockMgr.signinSilent).toHaveBeenCalled();
  });

  it('clears user when signinSilent fails on token expiry', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    let tokenExpiredCallback: (() => void) | undefined;
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue({ expired: false, access_token: 'old-token' }),
      signinSilent: vi.fn().mockRejectedValue(new Error('silent renew failed')),
      events: {
        addUserLoaded: vi.fn(),
        addUserUnloaded: vi.fn(),
        addAccessTokenExpired: vi.fn((cb: () => void) => {
          tokenExpiredCallback = cb;
        }),
        removeUserLoaded: vi.fn(),
        removeUserUnloaded: vi.fn(),
        removeAccessTokenExpired: vi.fn(),
      },
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    renderWithConfig();

    await screen.findByTestId('token');
    expect(screen.getByTestId('token')).toHaveTextContent('old-token');

    await act(async () => {
      tokenExpiredCallback?.();
    });

    expect(screen.getByTestId('token')).toHaveTextContent('none');
  });

  it('removes token provider on unmount when auth is enabled', async () => {
    vi.mocked(getOidcConfig).mockReturnValue(oidcConfig);
    const mockUser = { expired: false, access_token: 'test-token' };
    const mockMgr = createMockManager({
      getUser: vi.fn().mockResolvedValue(mockUser),
    });
    vi.mocked(getUserManager).mockReturnValue(mockMgr as never);

    const { unmount } = renderWithConfig();

    await screen.findByTestId('authenticated');
    unmount();

    expect(setTokenProvider).toHaveBeenLastCalledWith(null);
  });
});
