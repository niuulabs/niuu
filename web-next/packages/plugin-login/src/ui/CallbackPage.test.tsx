import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AuthContext, type AuthContextValue } from '@niuulabs/auth';
import { CallbackPage } from './CallbackPage';

const baseAuth: AuthContextValue = {
  enabled: true,
  authenticated: false,
  loading: true,
  user: null,
  accessToken: null,
  login: vi.fn(),
  logout: vi.fn(),
};

function wrap(auth: AuthContextValue) {
  return render(
    <AuthContext.Provider value={auth}>
      <CallbackPage />
    </AuthContext.Provider>,
  );
}

describe('CallbackPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders the callback page root', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('callback-page')).toBeInTheDocument();
  });

  it('renders the loading spinner', () => {
    wrap(baseAuth);
    expect(
      screen.getByTestId('callback-page').querySelector('.callback-page__spinner'),
    ).not.toBeNull();
  });

  it('renders the "Completing sign in…" label', () => {
    wrap(baseAuth);
    expect(screen.getByText('Completing sign in…')).toBeInTheDocument();
  });

  it('does not redirect while loading', () => {
    const replace = vi.fn();
    vi.stubGlobal('location', { replace });

    wrap({ ...baseAuth, loading: true });
    expect(replace).not.toHaveBeenCalled();
  });

  it('does not redirect when auth is disabled', () => {
    const replace = vi.fn();
    vi.stubGlobal('location', { replace });

    wrap({ ...baseAuth, loading: false, enabled: false, authenticated: false });
    expect(replace).not.toHaveBeenCalled();
  });

  it('does not redirect when not yet authenticated', () => {
    const replace = vi.fn();
    vi.stubGlobal('location', { replace });

    wrap({ ...baseAuth, loading: false, authenticated: false });
    expect(replace).not.toHaveBeenCalled();
  });

  it('redirects to / when authenticated and not loading', async () => {
    const replace = vi.fn();
    vi.stubGlobal('location', { replace });

    wrap({ ...baseAuth, loading: false, authenticated: true });

    await waitFor(() => {
      expect(replace).toHaveBeenCalledWith('/');
    });
  });
});
