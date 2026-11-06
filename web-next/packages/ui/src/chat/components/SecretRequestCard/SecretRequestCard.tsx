import { useEffect, useId, useState, type FormEvent } from 'react';
import { Eye, EyeOff, Lock } from 'lucide-react';
import {
  secretTitle,
  type SecretKeep,
  type SecretRequest,
  type SecretRequestInput,
} from '@niuulabs/domain';
import { cn } from '../../../utils/cn';
import { Field } from '../../../primitives/Field';
import { Input } from '../../../primitives/Input';
import { useSecretRequests, type SecretRequestsPort } from '../../../SecretRequests';

/** How often a pending request is re-read, so a decision made elsewhere shows up. */
export const SECRET_REQUEST_POLL_MS = 4_000;

const EMPTY_SECRET_ERROR = 'Enter the secret first';

const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:rounded-md niuu:border niuu:border-solid niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-medium niuu:cursor-pointer niuu:disabled:cursor-not-allowed niuu:disabled:opacity-60';

export interface SecretRequestCardProps {
  request: SecretRequestInput;
  className?: string;
}

/**
 * One "keep it for" choice. The global form reset strips the native radio's
 * appearance, so the radio stays for semantics and keyboard use while the pill
 * and its dot show which one is chosen.
 */
function KeepChoice({
  name,
  value,
  keep,
  onChoose,
  disabled,
  children,
}: {
  name: string;
  value: SecretKeep;
  keep: SecretKeep;
  onChoose: (value: SecretKeep) => void;
  disabled: boolean;
  children: string;
}) {
  const chosen = keep === value;
  return (
    <label
      className={cn(
        'niuu:flex niuu:cursor-pointer niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-solid niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:focus-within:border-brand',
        chosen
          ? 'niuu:border-brand niuu:bg-brand/10 niuu:text-text-primary'
          : 'niuu:border-border-subtle niuu:text-text-secondary',
        disabled && 'niuu:cursor-not-allowed niuu:opacity-60',
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={chosen}
        onChange={() => onChoose(value)}
        disabled={disabled}
        className="niuu:sr-only"
      />
      <span
        aria-hidden="true"
        className={cn(
          'niuu:flex niuu:h-3.5 niuu:w-3.5 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-solid',
          chosen ? 'niuu:border-brand' : 'niuu:border-border-subtle',
        )}
      >
        {chosen && <span className="niuu:h-1.5 niuu:w-1.5 niuu:rounded-full niuu:bg-brand" />}
      </span>
      {children}
    </label>
  );
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function Heading({ request, className }: { request: SecretRequestInput; className?: string }) {
  return (
    <>
      <header className={cn('niuu:flex niuu:items-center niuu:gap-2', className)}>
        <Lock className="niuu:h-4 niuu:w-4 niuu:shrink-0 niuu:text-brand" aria-hidden="true" />
        <h3 className="niuu:m-0 niuu:min-w-0 niuu:flex-1 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
          {secretTitle(request.name)}
        </h3>
        <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted niuu:break-all">
          {request.name}
        </span>
      </header>
      {request.purpose && (
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">{request.purpose}</p>
      )}
      {request.target && (
        <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">for {request.target}</p>
      )}
    </>
  );
}

function Outcome({ request }: { request: SecretRequest }) {
  if (request.status === 'declined') {
    return (
      <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
        Declined. The agent was told no secret is coming.
      </p>
    );
  }
  if (request.status === 'cancelled') {
    return (
      <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">This request was withdrawn.</p>
    );
  }
  const kept =
    request.keep === 'saved'
      ? request.credentialName
        ? `Kept in your credentials as ${request.credentialName}.`
        : 'Kept in your credentials.'
      : 'Deleted when this session stops.';
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-sm niuu:text-text-secondary">
      <p className="niuu:m-0 niuu:text-state-ok">Saved. The value was never shown to the agent.</p>
      <p className="niuu:m-0">{kept}</p>
    </div>
  );
}

function SecretForm({
  request,
  port,
  onResolved,
}: {
  request: SecretRequestInput;
  port: SecretRequestsPort;
  onResolved: (state: SecretRequest) => void;
}) {
  const baseId = useId();
  // The value lives only here: never in a query key, cache, log or the transcript.
  const [value, setValue] = useState('');
  const [visible, setVisible] = useState(false);
  const [keep, setKeep] = useState<SecretKeep>('session');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (value.trim() === '') {
      setFieldError(EMPTY_SECRET_ERROR);
      return;
    }
    setFieldError(null);
    setActionError(null);
    setBusy(true);
    try {
      const resolved = await port.fulfill(request.requestId, { value, keep });
      setValue('');
      setBusy(false);
      onResolved(resolved);
    } catch (error) {
      setActionError(messageOf(error));
      setBusy(false);
    }
  }

  async function decline() {
    if (busy) return;
    setFieldError(null);
    setActionError(null);
    setBusy(true);
    try {
      const resolved = await port.decline(request.requestId);
      setValue('');
      setBusy(false);
      onResolved(resolved);
    } catch (error) {
      setActionError(messageOf(error));
      setBusy(false);
    }
  }

  return (
    <form className="niuu:flex niuu:flex-col niuu:gap-3" onSubmit={submit} noValidate>
      <Field label="Secret" error={fieldError ?? undefined}>
        <div className="niuu:flex niuu:items-center niuu:gap-2">
          <Input
            type={visible ? 'text' : 'password'}
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              setFieldError(null);
            }}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            disabled={busy}
            className="niuu:flex-1 niuu:font-mono"
          />
          <button
            type="button"
            onClick={() => setVisible((shown) => !shown)}
            aria-label={visible ? 'Hide secret' : 'Show secret'}
            aria-pressed={visible}
            className="niuu:inline-flex niuu:cursor-pointer niuu:rounded-md niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-tertiary niuu:p-2 niuu:text-text-secondary"
          >
            {visible ? (
              <EyeOff className="niuu:h-4 niuu:w-4" aria-hidden="true" />
            ) : (
              <Eye className="niuu:h-4 niuu:w-4" aria-hidden="true" />
            )}
          </button>
        </div>
      </Field>

      <fieldset className="niuu:m-0 niuu:flex niuu:flex-wrap niuu:gap-2 niuu:border-0 niuu:p-0">
        <legend className="niuu:mb-1 niuu:p-0 niuu:text-xs niuu:text-text-muted">
          Keep it for
        </legend>
        <KeepChoice
          name={`${baseId}-keep`}
          value="session"
          keep={keep}
          onChoose={setKeep}
          disabled={busy}
        >
          This session only
        </KeepChoice>
        <KeepChoice
          name={`${baseId}-keep`}
          value="saved"
          keep={keep}
          onChoose={setKeep}
          disabled={busy}
        >
          Save to my credentials
        </KeepChoice>
      </fieldset>

      {actionError && (
        <p role="alert" className="niuu:m-0 niuu:text-sm niuu:text-critical">
          {actionError}
        </p>
      )}

      <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
        <button
          type="submit"
          disabled={busy}
          className={cn(BUTTON, 'niuu:border-brand niuu:bg-brand niuu:text-bg-primary')}
        >
          Save securely
        </button>
        <button
          type="button"
          onClick={decline}
          disabled={busy}
          className={cn(BUTTON, 'niuu:border-border niuu:bg-transparent niuu:text-text-secondary')}
        >
          Decline
        </button>
        <span className="niuu:text-xs niuu:text-text-muted">Never shown to the agent</span>
      </div>
    </form>
  );
}

/**
 * Inline card for a `secret_request` turn: the agent asks for a secret and the person types it
 * into a masked field. The value goes through the host's port to Forge; the card holds it only
 * in the input and clears it once it is sent. Without a port the card is read-only.
 */
export function SecretRequestCard({ request, className }: SecretRequestCardProps) {
  const port = useSecretRequests();
  const [state, setState] = useState<SecretRequest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!port) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const read = async () => {
      let next: SecretRequest;
      try {
        next = await port.get(request.requestId);
      } catch (error) {
        if (!cancelled) setLoadError(messageOf(error));
        return;
      }
      if (cancelled) return;
      setLoadError(null);
      setState(next);
      if (next.status === 'pending') timer = setTimeout(read, SECRET_REQUEST_POLL_MS);
    };

    void read();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [port, request.requestId, attempt]);

  const shell = cn(
    'niuu:my-3 niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-lg niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3',
    className,
  );

  const label = `Secret request: ${request.name}`;

  if (!port) {
    return (
      <article
        data-testid="secret-request-card"
        data-state="read-only"
        aria-label={label}
        className={shell}
      >
        <Heading request={request} />
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">Answer this in Forge</p>
      </article>
    );
  }

  return (
    <article
      data-testid="secret-request-card"
      data-state={state?.status ?? (loadError ? 'error' : 'loading')}
      aria-label={label}
      className={shell}
    >
      <Heading request={request} />
      {state === null && !loadError && (
        <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">Checking this request...</p>
      )}
      {state?.status === 'pending' && (
        <SecretForm request={request} port={port} onResolved={setState} />
      )}
      {state && state.status !== 'pending' && <Outcome request={state} />}
      {loadError && (
        <div className="niuu:flex niuu:flex-col niuu:items-start niuu:gap-2">
          <p role="alert" className="niuu:m-0 niuu:text-sm niuu:text-critical">
            {loadError}
          </p>
          <button
            type="button"
            onClick={() => setAttempt((count) => count + 1)}
            className={cn(
              BUTTON,
              'niuu:border-border niuu:bg-transparent niuu:text-text-secondary',
            )}
          >
            Try again
          </button>
        </div>
      )}
    </article>
  );
}
