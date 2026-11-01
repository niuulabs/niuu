import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AuthContext, type AuthContextValue } from '@niuulabs/auth';
import { LoginPage, buildBannerText } from './LoginPage';

const baseAuth: AuthContextValue = {
  enabled: true,
  authenticated: false,
  loading: false,
  user: null,
  accessToken: null,
  login: vi.fn(),
  logout: vi.fn(),
};

function wrap(auth: AuthContextValue, props?: Parameters<typeof LoginPage>[0]) {
  return render(
    <AuthContext.Provider value={auth}>
      <LoginPage {...props} />
    </AuthContext.Provider>,
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the login page root', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('renders the niuu wordmark', () => {
    wrap(baseAuth);
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument();
  });

  it('renders the primary sign-in button', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('sign-in-btn')).toBeInTheDocument();
    expect(screen.getByTestId('sign-in-btn')).not.toBeDisabled();
  });

  it('calls login() when the primary button is clicked', () => {
    const login = vi.fn();
    wrap({ ...baseAuth, login });
    fireEvent.click(screen.getByTestId('sign-in-btn'));
    expect(login).toHaveBeenCalledOnce();
  });

  it('disables the sign-in button when loading', () => {
    wrap({ ...baseAuth, loading: true });
    expect(screen.getByTestId('sign-in-btn')).toBeDisabled();
  });

  it('shows "Continuing to Niuu Identity…" text when loading', () => {
    wrap({ ...baseAuth, loading: true });
    expect(screen.getByText('Continuing to Niuu Identity…')).toBeInTheDocument();
  });

  it('shows "Continue to sign in" text when not loading', () => {
    wrap(baseAuth);
    expect(screen.getByText('Continue to sign in')).toBeInTheDocument();
  });

  it('shows the direction arrow when not loading', () => {
    wrap(baseAuth);
    expect(screen.getByText('→')).toBeInTheDocument();
  });

  it('does not call login() when primary button is clicked while loading', () => {
    const login = vi.fn();
    wrap({ ...baseAuth, loading: true, login });
    fireEvent.click(screen.getByTestId('sign-in-btn'));
    expect(login).not.toHaveBeenCalled();
  });

  it('does not show an error block when there is no OIDC error', () => {
    wrap(baseAuth);
    expect(screen.queryByTestId('login-error')).not.toBeInTheDocument();
  });

  it('shows an error block when oidcError prop is provided', () => {
    wrap(baseAuth, { oidcError: 'access_denied' });
    expect(screen.getByTestId('login-error')).toBeInTheDocument();
    expect(screen.getByText('Authentication failed')).toBeInTheDocument();
  });

  it('shows error description when oidcErrorDescription prop is provided', () => {
    wrap(baseAuth, {
      oidcError: 'access_denied',
      oidcErrorDescription: 'User denied access',
    });
    expect(screen.getByText('User denied access')).toBeInTheDocument();
  });

  it('reads oidcError from window.location.search when no prop is given', () => {
    vi.stubGlobal('location', {
      ...window.location,
      search: '?error=login_required&error_description=Session+expired',
    });

    wrap(baseAuth);
    expect(screen.getByTestId('login-error')).toBeInTheDocument();
    expect(screen.getByText('Session expired')).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it('shows the build banner', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('build-banner')).toBeInTheDocument();
  });

  it('explains the identity handoff and shows one sign-in action', () => {
    wrap(baseAuth);
    expect(screen.getByText('Where AI agents work, collaborate, and evolve.')).toBeVisible();
    expect(screen.getByText('You’ll continue to Niuu Identity.')).toBeVisible();
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  it('renders the logo SVG', () => {
    const { container } = wrap(baseAuth);
    expect(container.querySelector('svg')).not.toBeNull();
  });
});

describe('buildBannerText', () => {
  it('returns just niuu when no version', () => {
    expect(buildBannerText('', '')).toBe('niuu');
  });

  it('returns version without realm', () => {
    expect(buildBannerText('2026.04.18-7f3a2c', '')).toBe('niuu · build 2026.04.18-7f3a2c');
  });

  it('returns version and realm', () => {
    expect(buildBannerText('2026.04.18-7f3a2c', 'valaskjálf')).toBe(
      'niuu · build 2026.04.18-7f3a2c · valaskjálf',
    );
  });
});
