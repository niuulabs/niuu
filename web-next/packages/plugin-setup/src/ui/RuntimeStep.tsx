import { accessMode, accessUrls, type HostFacts, type SetupState } from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';

export interface RuntimeStepProps {
  facts: HostFacts | null;
  state: SetupState | undefined;
}

/**
 * Runtime & access: how sessions run and who can reach this install.
 *
 * Everything here is read from how `niuu up` started the stack. Changing it
 * is a config-file edit plus `niuu up`, which the step spells out instead of
 * pretending a browser toggle could re-bind the published port.
 */
export function RuntimeStep({ facts, state }: RuntimeStepProps) {
  const mode = accessMode(facts);
  const urls = accessUrls(facts);
  const signInOff = state?.mode === 'docker' || state?.mode === 'mini';
  return (
    <div className="setup-col" data-testid="setup-runtime">
      <div className="setup-card">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">Sessions run in containers</h3>
            <p className="setup-card__desc">
              Every session gets its own container on this host: a private workspace and home, no
              access to the platform&apos;s environment, and only the credentials its integrations
              ask for.
            </p>
          </div>
          <span className="setup-chip setup-chip--ok">
            <CheckIcon size={12} /> Isolated
          </span>
        </div>
        <div className="setup-chips">
          {facts?.skuld_image ? (
            <span className="setup-chip" data-testid="setup-runtime-image">
              {facts.skuld_image}
            </span>
          ) : null}
          <span className="setup-chip">credentials mounted read-only, per session</span>
        </div>
      </div>

      <div className="setup-card" data-testid={`setup-access-${mode}`}>
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">Who can reach this install</h3>
            <p className="setup-card__desc">
              {mode === 'local'
                ? 'Only this machine. The web app listens on 127.0.0.1.'
                : mode === 'lan'
                  ? 'Anyone on your network can open the web app.'
                  : 'Start the platform with `niuu up` to record how it was published.'}
            </p>
          </div>
          <span className={`setup-chip ${mode === 'local' ? 'setup-chip--ok' : ''}`}>
            {mode === 'local' ? 'This machine only' : mode === 'lan' ? 'Network' : 'Unknown'}
          </span>
        </div>
        {urls.length > 0 ? (
          <div className="setup-chips" data-testid="setup-access-urls">
            {urls.map((url) => (
              <span key={url} className="setup-chip setup-chip--brand">
                {url}
              </span>
            ))}
          </div>
        ) : null}
        <div className="setup-row">
          <span
            className={`setup-row__icon ${signInOff ? 'setup-row__icon--warn' : 'setup-row__icon--ok'}`}
          >
            {signInOff ? <AlertIcon /> : <CheckIcon />}
          </span>
          <div className="setup-row__body">
            <span className="setup-row__title">Sign-in is {signInOff ? 'off' : 'on'}</span>
            <span className="setup-row__detail">
              {signInOff
                ? mode === 'lan'
                  ? 'Anyone who can reach the address above can use Niuu as you. Keep this to a trusted network, or bind to this machine only until you turn on OIDC sign-in in Settings → Access.'
                  : 'Fine for a single-user machine. Turn on OIDC sign-in in Settings → Access before opening it to a network.'
                : 'Requests are authenticated by your identity provider.'}
            </span>
          </div>
        </div>
        <div className="setup-row">
          <span className="setup-row__icon">⌘</span>
          <div className="setup-row__body">
            <span className="setup-row__title">To change this</span>
            <span className="setup-row__detail">
              Set <code>docker.bind_host</code> in <code>~/.niuu/config.yaml</code> (
              <code>127.0.0.1</code> for this machine only, <code>0.0.0.0</code> for the network)
              and run <code>niuu up</code> again.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
