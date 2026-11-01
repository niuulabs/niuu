import { useAuth } from '@niuulabs/auth';
import { ContinuityField, NiuuMark, NiuuWordmark } from '@niuulabs/ui';
import './LoginPage.css';

const EMBLEM_MARK_SIZE = 57;
const EMBLEM_WORDMARK_HEIGHT = 34;
const FEATURES = ['Autonomous', 'Collaborative', 'Persistent', 'Observable', 'Self-hosted'];

interface LoginPageProps {
  /** Override the OIDC error code (default: read from ?error= URL param). */
  oidcError?: string;
  /** Override the OIDC error description (default: read from ?error_description= URL param). */
  oidcErrorDescription?: string;
}

/** The NIUU primary lockup: orbit mark and the path-drawn wordmark. */
function Emblem() {
  return (
    <div className="login-page__emblem">
      <NiuuMark size={EMBLEM_MARK_SIZE} />
      <h1 className="login-page__wordmark" aria-label="NIUU">
        <NiuuWordmark height={EMBLEM_WORDMARK_HEIGHT} />
      </h1>
    </div>
  );
}

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
 * On wide screens it is the same stage as Niuu Identity, so pressing the button
 * only changes what is in the panel. On narrow screens the same elements become
 * one centred column around the lockup; see LoginPage.css.
 *
 * Uses `position: fixed` to overlay the Shell layout when rendered inside it.
 */
export function LoginPage({
  oidcError: errorProp,
  oidcErrorDescription: descProp,
}: LoginPageProps = {}) {
  const { login, loading } = useAuth();

  const params =
    typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search)
      : new URLSearchParams();
  const oidcError = errorProp ?? params.get('error');
  const oidcErrorDescription = descProp ?? params.get('error_description');

  return (
    <div className="login-page" data-testid="login-page">
      <ContinuityField composition="hero" />

      <header className="login-page__head">
        <Emblem />
        <div className="login-page__build" data-testid="build-banner">
          {buildBannerText()}
        </div>
      </header>

      <div className="login-page__main">
        <section className="login-page__copy">
          <div className="login-page__kicker">niuu · autonomous work</div>
          <p className="login-page__tag">
            A place where <br />
            intelligent systems <br />
            do real work.
          </p>
        </section>

        <main className="login-page__panel">
          <h2 className="login-page__panel-title">Sign in</h2>

          {oidcError && (
            <div className="login-page__error" role="alert" data-testid="login-error">
              <span className="login-page__error-title">Authentication failed</span>
              {oidcErrorDescription && (
                <span className="login-page__error-desc">{oidcErrorDescription}</span>
              )}
            </div>
          )}

          <p className="login-page__handoff">You’ll continue to Niuu Identity.</p>

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
        </main>
      </div>

      <footer className="login-page__strip">
        <div className="login-page__features">
          {FEATURES.map((feature, index) => (
            <span key={feature}>
              {index !== 0 && <b>·</b>}
              {feature}
            </span>
          ))}
        </div>
        <span className="login-page__tagline">Continuity / not a session</span>
      </footer>
    </div>
  );
}
