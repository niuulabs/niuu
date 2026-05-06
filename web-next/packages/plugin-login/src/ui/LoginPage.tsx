import type { ComponentType } from 'react';
import { useAuth } from '@niuulabs/auth';
import { AmbientTopology } from './AmbientTopology';
import { AmbientConstellation } from './AmbientConstellation';
import { AmbientLattice } from './AmbientLattice';
import { LogoKnot } from './LogoKnot';
import { useAmbient, type AmbientVariant } from './useAmbient';
import { GithubIcon } from './icons/GithubIcon';
import { GoogleIcon } from './icons/GoogleIcon';
import './LoginPage.css';

interface LoginPageProps {
  /** Override the OIDC error code (default: read from ?error= URL param). */
  oidcError?: string;
  /** Override the OIDC error description (default: read from ?error_description= URL param). */
  oidcErrorDescription?: string;
  /** Force a specific ambient variant (default: reads/writes localStorage). */
  ambient?: AmbientVariant;
}

const AMBIENT_MAP: Record<AmbientVariant, ComponentType> = {
  topology: AmbientTopology,
  constellation: AmbientConstellation,
  lattice: AmbientLattice,
};

function LockIcon() {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect width={18} height={11} x={3} y={11} rx={2} ry={2} />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

const buildVersion = (import.meta.env['VITE_BUILD_VERSION'] as string | undefined) ?? '';
const buildRealm = (import.meta.env['VITE_BUILD_REALM'] as string | undefined) ?? '';

export function buildBannerText(version = buildVersion, realm = buildRealm): string {
  if (!version) return 'niuu';
  if (!realm) return `niuu · build ${version}`;
  return `niuu · build ${version} · ${realm}`;
}

/**
 * Full-viewport login page.
 *
 * Shows the niuu knot rune, wordmark, and sign-in buttons that
 * kick off the configured OIDC flow via `useAuth().login()`.
 *
 * Uses `position: fixed` to overlay the Shell layout when rendered inside it.
 */
export function LoginPage({
  oidcError: errorProp,
  oidcErrorDescription: descProp,
  ambient: ambientProp,
}: LoginPageProps = {}) {
  const { login, loading } = useAuth();
  const [storedAmbient] = useAmbient();
  const activeAmbient = ambientProp ?? storedAmbient;
  const AmbientComponent = AMBIENT_MAP[activeAmbient];

  const params =
    typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search)
      : new URLSearchParams();
  const oidcError = errorProp ?? params.get('error');
  const oidcErrorDescription = descProp ?? params.get('error_description');

  return (
    <div className="login-page" data-testid="login-page">
      <AmbientComponent />

      <div className="login-page__build login-page__mono" data-testid="build-banner">
        <span className="login-page__build-dot" aria-hidden />
        {buildBannerText()}
      </div>

      <main className="login-page__card">
        <div className="login-page__mark">
          <LogoKnot size={72} stroke={1.6} glow />
        </div>

        <h1 className="login-page__wordmark">
          <span className="login-page__wordmark-n">n</span>iuu
        </h1>

        <p className="login-page__tag login-page__mono">agentic infrastructure</p>

        <div className="login-page__divider">
          <span>sign in</span>
        </div>

        {oidcError && (
          <div className="login-page__error" role="alert" data-testid="login-error">
            <span className="login-page__error-title">Authentication failed</span>
            {oidcErrorDescription && (
              <span className="login-page__error-desc">{oidcErrorDescription}</span>
            )}
          </div>
        )}

        <div className="login-page__auth">
          <button
            className="login-page__btn"
            onClick={loading ? undefined : login}
            disabled={loading}
            aria-label={
              loading ? 'Redirecting to identity provider…' : 'Sign in with your identity provider'
            }
            data-testid="sign-in-btn"
          >
            {loading ? <span className="login-page__spinner" aria-hidden /> : <LockIcon />}
            <span>{loading ? 'redirecting…' : 'Continue with passkey'}</span>
            {!loading && (
              <span className="login-page__kbd login-page__mono" aria-hidden>
                ↵
              </span>
            )}
          </button>

          <div className="login-page__oauth-row" data-testid="oauth-row">
            <button
              className="login-page__btn login-page__btn--ghost"
              onClick={loading ? undefined : login}
              disabled={loading}
              aria-label="Sign in with GitHub"
              data-testid="github-btn"
            >
              <GithubIcon />
              <span>GitHub</span>
            </button>
            <button
              className="login-page__btn login-page__btn--ghost"
              onClick={loading ? undefined : login}
              disabled={loading}
              aria-label="Sign in with Google"
              data-testid="google-btn"
            >
              <GoogleIcon />
              <span>Google</span>
            </button>
          </div>
        </div>

        <div className="login-page__foot login-page__mono" data-testid="request-access-footer">
          <span className="login-page__foot-dim">no account?</span>
          <a href="#" className="login-page__link" data-testid="request-access-link">
            request access
          </a>
        </div>
      </main>
    </div>
  );
}
