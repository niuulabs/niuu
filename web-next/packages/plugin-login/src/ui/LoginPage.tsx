import type { ComponentType } from 'react';
import { useAuth } from '@niuulabs/auth';
import { AmbientTopology } from './AmbientTopology';
import { AmbientConstellation } from './AmbientConstellation';
import { AmbientLattice } from './AmbientLattice';
import { LoginScene, Emblem } from './LoginScene';
import type { AmbientVariant } from './useAmbient';
import './LoginPage.css';

interface LoginPageProps {
  /** Override the OIDC error code (default: read from ?error= URL param). */
  oidcError?: string;
  /** Override the OIDC error description (default: read from ?error_description= URL param). */
  oidcErrorDescription?: string;
  /** Optional legacy ambient background override. */
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
 * Shows the niuu cube constellation and a sign-in button that
 * kicks off the configured OIDC flow via `useAuth().login()`.
 *
 * Uses `position: fixed` to overlay the Shell layout when rendered inside it.
 */
export function LoginPage({
  oidcError: errorProp,
  oidcErrorDescription: descProp,
  ambient: ambientProp,
}: LoginPageProps = {}) {
  const { login, loading } = useAuth();
  const AmbientComponent = ambientProp ? AMBIENT_MAP[ambientProp] : LoginScene;

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
        {buildBannerText()}
      </div>

      <main className="login-page__card">
        <div className="login-page__kicker">níu · the ninth world</div>
        <Emblem />
        <p className="login-page__tag">Where AI agents work, collaborate, and evolve.</p>

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
            aria-label={loading ? 'Redirecting to identity provider…' : 'Continue to sign in'}
            data-testid="sign-in-btn"
          >
            {loading ? <span className="login-page__spinner" aria-hidden /> : <LockIcon />}
            <span>{loading ? 'Continuing to Niuu Identity…' : 'Continue to sign in'}</span>
            {!loading && <span aria-hidden>→</span>}
          </button>
        </div>
        <p className="login-page__handoff">You’ll continue to Niuu Identity.</p>
      </main>
    </div>
  );
}
