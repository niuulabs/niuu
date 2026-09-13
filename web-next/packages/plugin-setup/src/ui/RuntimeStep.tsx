import {
  errorMessage,
  LOCAL_BIND_HOST,
  NETWORK_BIND_HOST,
  type StackChanges,
  type StackView,
} from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';

export interface RuntimeStepProps {
  stack: StackView | undefined;
  loading: boolean;
  /** Set when the install has no stack controller (not started with `niuu up`). */
  unavailable: Error | null;
  staging: boolean;
  stageError: Error | null;
  onStage: (changes: StackChanges) => void;
}

interface OptionProps {
  title: string;
  description: string;
  selected: boolean;
  disabled?: boolean;
  aside?: React.ReactNode;
  testId: string;
  onSelect?: () => void;
}

function Option({ title, description, selected, disabled, aside, testId, onSelect }: OptionProps) {
  return (
    <button
      type="button"
      className={`setup-option ${selected ? 'setup-option--selected' : ''}`}
      onClick={onSelect}
      disabled={disabled || !onSelect}
      aria-pressed={selected}
      data-testid={testId}
    >
      <span className="setup-option__radio" />
      <span className="setup-option__body">
        <span className="setup-option__title">{title}</span>
        <span className="setup-option__desc">{description}</span>
      </span>
      {aside ? <span className="setup-option__aside setup-chips">{aside}</span> : null}
    </button>
  );
}

/**
 * Runtime & access: where sessions execute and who can reach this Niuu.
 *
 * Choices are staged through the stack controller and applied on the finish
 * step, which restarts the services whose configuration changed.
 */
export function RuntimeStep({
  stack,
  loading,
  unavailable,
  staging,
  stageError,
  onStage,
}: RuntimeStepProps) {
  const bind = stack?.effective.bindHost ?? '';
  const urls = stack?.effective.accessUrls ?? [];
  const lanUrl =
    urls[1] ?? (stack ? `http://${stack.effective.externalHost}:${stack.effective.port}` : '');
  return (
    <div className="setup-two" data-testid="setup-runtime">
      <div className="setup-card">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">Where sessions run</h3>
            <p className="setup-card__desc">
              Each session gets its own runtime with the GPU, your credentials and the workspace
              mounted in.
            </p>
          </div>
        </div>
        <Option
          title="Docker container"
          description="One container per session on this host: private workspace and home, no access to the platform's environment, only the credentials its integrations ask for."
          selected
          onSelect={() => undefined}
          testId="setup-runtime-docker"
          aside={
            <span className="setup-chip setup-chip--ok">
              <CheckIcon size={12} /> Isolated
            </span>
          }
        />
        <Option
          title="OpenShell sandbox"
          description="NVIDIA OpenShell sandbox per session with a network policy. Available on the OpenShell install profile; not in this bundle yet."
          selected={false}
          disabled
          testId="setup-runtime-openshell"
          aside={<span className="setup-chip">Later</span>}
        />
        <Option
          title="Host process"
          description="Runs the agent CLIs directly on this machine. No isolation; not offered on a shared host."
          selected={false}
          disabled
          testId="setup-runtime-host"
          aside={
            <span className="setup-chip setup-chip--warn">
              <AlertIcon size={12} /> Not isolated
            </span>
          }
        />
        {stack?.effective.skuldImage ? (
          <div className="setup-note" data-testid="setup-runtime-image">
            Session image {stack.effective.skuldImage}
          </div>
        ) : null}
      </div>

      <div className="setup-card" data-testid="setup-access">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">Who can reach this Niuu</h3>
            <p className="setup-card__desc">
              Sign-in is off by default. Turn it on later in Settings → Access.
            </p>
          </div>
        </div>
        {unavailable ? (
          <div className="setup-note setup-note--warn" data-testid="setup-access-unavailable">
            <AlertIcon size={13} /> {errorMessage(unavailable)}
          </div>
        ) : null}
        {loading ? <div className="setup-note">Reading how the stack was started…</div> : null}
        {stack ? (
          <>
            <Option
              title="Only this machine"
              description={urls[0] ?? ''}
              selected={bind === LOCAL_BIND_HOST}
              disabled={staging}
              testId="setup-access-local"
              onSelect={() => onStage({ bind_host: LOCAL_BIND_HOST })}
            />
            <Option
              title="Your local network"
              description={`${lanUrl} · anyone on the LAN`}
              selected={bind === NETWORK_BIND_HOST}
              disabled={staging}
              testId="setup-access-lan"
              onSelect={() => onStage({ bind_host: NETWORK_BIND_HOST })}
            />
            <Option
              title="Public, behind sign-in"
              description="Reverse proxy with HTTPS and an identity provider (OIDC). Set up later in Settings → Access."
              selected={false}
              disabled
              testId="setup-access-public"
              aside={<span className="setup-chip">Later</span>}
            />
            {bind === NETWORK_BIND_HOST ? (
              <div className="setup-note setup-note--warn" data-testid="setup-access-warning">
                <AlertIcon size={13} /> Anyone who can open {lanUrl} can start sessions on this host
                as you. Keep it to a trusted network until sign-in is on.
              </div>
            ) : null}
            {stack.current.bindHost !== stack.effective.bindHost ? (
              <div className="setup-note" data-testid="setup-access-staged">
                Applied when you finish setup; the platform restarts for it.
              </div>
            ) : null}
          </>
        ) : null}
        {stageError ? (
          <div className="setup-error" role="alert">
            {errorMessage(stageError)}
          </div>
        ) : null}
      </div>
    </div>
  );
}
