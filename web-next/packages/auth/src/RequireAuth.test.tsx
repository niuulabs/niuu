import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AuthContext, type AuthContextValue } from './AuthContext';
import { RequireAuth } from './RequireAuth';

const baseAuthValue: AuthContextValue = {
  enabled: false,
  authenticated: false,
  loading: false,
  user: null,
  accessToken: null,
  login: vi.fn(),
  logout: vi.fn(),
};

function renderWithAuth(value: AuthContextValue, loginPath?: string) {
  return render(
    <AuthContext.Provider value={value}>
      <RequireAuth loginPath={loginPath}>
        <div data-testid="protected">Protected content</div>
      </RequireAuth>
    </AuthContext.Provider>,
  );
}

describe('RequireAuth', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, 'location', {
      value: { replace: vi.fn() },
      writable: true,
    });
  });

  it('renders children when auth is disabled', () => {
    renderWithAuth({ ...baseAuthValue, enabled: false, authenticated: false });

    expect(screen.getByTestId('protected')).toBeInTheDocument();
    expect(window.location.replace).not.toHaveBeenCalled();
  });

  it('renders children when authenticated', () => {
    renderWithAuth({ ...baseAuthValue, enabled: true, authenticated: true });

    expect(screen.getByTestId('protected')).toBeInTheDocument();
    expect(window.location.replace).not.toHaveBeenCalled();
  });

  it('renders nothing while loading', () => {
    renderWithAuth({ ...baseAuthValue, enabled: true, authenticated: false, loading: true });

    expect(screen.queryByTestId('protected')).toBeNull();
    expect(window.location.replace).not.toHaveBeenCalled();
  });

  it('redirects to /login when auth is enabled and user is not authenticated', async () => {
    renderWithAuth({ ...baseAuthValue, enabled: true, authenticated: false, loading: false });

    expect(screen.queryByTestId('protected')).toBeNull();
    expect(window.location.replace).toHaveBeenCalledWith('/login');
  });

  it('redirects to custom loginPath when provided', async () => {
    renderWithAuth(
      { ...baseAuthValue, enabled: true, authenticated: false, loading: false },
      '/auth/signin',
    );

    expect(window.location.replace).toHaveBeenCalledWith('/auth/signin');
  });

  it('does not redirect again when loading transitions to false with auth enabled', () => {
    const { rerender } = render(
      <AuthContext.Provider value={{ ...baseAuthValue, enabled: true, loading: true }}>
        <RequireAuth>
          <div data-testid="protected">Protected content</div>
        </RequireAuth>
      </AuthContext.Provider>,
    );

    expect(window.location.replace).not.toHaveBeenCalled();

    rerender(
      <AuthContext.Provider
        value={{ ...baseAuthValue, enabled: true, authenticated: true, loading: false }}
      >
        <RequireAuth>
          <div data-testid="protected">Protected content</div>
        </RequireAuth>
      </AuthContext.Provider>,
    );

    expect(screen.getByTestId('protected')).toBeInTheDocument();
    expect(window.location.replace).not.toHaveBeenCalled();
  });
});
