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

  it('renders the passkey sign-in button', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('sign-in-btn')).toBeInTheDocument();
    expect(screen.getByTestId('sign-in-btn')).not.toBeDisabled();
  });

  it('renders the GitHub button', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('github-btn')).toBeInTheDocument();
    expect(screen.getByTestId('github-btn')).not.toBeDisabled();
  });

  it('renders the Google button', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('google-btn')).toBeInTheDocument();
    expect(screen.getByTestId('google-btn')).not.toBeDisabled();
  });

  it('renders the OAuth row', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('oauth-row')).toBeInTheDocument();
  });

  it('calls login() when the passkey button is clicked', () => {
    const login = vi.fn();
    wrap({ ...baseAuth, login });
    fireEvent.click(screen.getByTestId('sign-in-btn'));
    expect(login).toHaveBeenCalledOnce();
  });

  it('calls login() when the GitHub button is clicked', () => {
    const login = vi.fn();
    wrap({ ...baseAuth, login });
    fireEvent.click(screen.getByTestId('github-btn'));
    expect(login).toHaveBeenCalledOnce();
  });

  it('calls login() when the Google button is clicked', () => {
    const login = vi.fn();
    wrap({ ...baseAuth, login });
    fireEvent.click(screen.getByTestId('google-btn'));
    expect(login).toHaveBeenCalledOnce();
  });

  it('disables the sign-in button when loading', () => {
    wrap({ ...baseAuth, loading: true });
    expect(screen.getByTestId('sign-in-btn')).toBeDisabled();
  });

  it('disables the GitHub button when loading', () => {
    wrap({ ...baseAuth, loading: true });
    expect(screen.getByTestId('github-btn')).toBeDisabled();
  });

  it('disables the Google button when loading', () => {
    wrap({ ...baseAuth, loading: true });
    expect(screen.getByTestId('google-btn')).toBeDisabled();
  });

  it('shows "redirecting…" text when loading', () => {
    wrap({ ...baseAuth, loading: true });
    expect(screen.getByText('redirecting…')).toBeInTheDocument();
  });

  it('shows "Continue with passkey" text when not loading', () => {
    wrap(baseAuth);
    expect(screen.getByText('Continue with passkey')).toBeInTheDocument();
  });

  it('shows the keyboard hint badge when not loading', () => {
    wrap(baseAuth);
    expect(screen.getByText('↵')).toBeInTheDocument();
  });

  it('does not call login() when passkey button is clicked while loading', () => {
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

  it('renders the "sign in" divider label', () => {
    wrap(baseAuth);
    expect(screen.getByText('sign in')).toBeInTheDocument();
  });

  it('shows the build banner', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('build-banner')).toBeInTheDocument();
  });

  it('shows "no account?" text in footer', () => {
    wrap(baseAuth);
    expect(screen.getByText('no account?')).toBeInTheDocument();
  });

  it('shows "request access" link in footer', () => {
    wrap(baseAuth);
    const link = screen.getByTestId('request-access-link');
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '#');
  });

  it('renders the footer element', () => {
    wrap(baseAuth);
    expect(screen.getByTestId('request-access-footer')).toBeInTheDocument();
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
