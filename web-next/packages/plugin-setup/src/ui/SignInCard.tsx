import { useEffect, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Field, Input } from '@niuulabs/ui';
import {
  connectionNeedsSignIn,
  buildConfigPayload,
  credentialNameFor,
  enrollmentFailureMessage,
  errorMessage,
  isEnrollmentActive,
  missingConfigKeys,
  type CatalogEntry,
  type Enrollment,
  type IntegrationConnection,
  type IntegrationTestResult,
} from '../domain/setup';
import { TestOutcome } from './TestOutcome';
import { AlertIcon, CheckIcon } from './icons';
import {
  ENROLLMENT_POLL_MS,
  useCancelEnrollment,
  useEnrollment,
  useStartEnrollment,
  useStartOAuthAuthorization,
  useSubmitEnrollmentCode,
  setupKeys,
} from './hooks';

export interface SignInCardProps {
  entry: CatalogEntry;
  connection: IntegrationConnection | undefined;
  /** Render only the body; the enclosing pane shows the title and status. */
  headless?: boolean;
  /** The credential this account signs in under; defaults to the entry's default name. */
  credentialName?: string;
  /** Hold the start button, e.g. while the account name clashes with an existing one. */
  disabled?: boolean;
  /** Which of the provider's OAuth applications this account signs in through. */
  oauthApp?: string;
  testResult?: IntegrationTestResult;
  testing?: boolean;
  onTest?: (connectionId: string) => void;
}

/**
 * A catalog entry that is connected by signing in through the provider.
 *
 * The wizard either runs the provider's device/CLI enrollment or opens its
 * standard OAuth authorization-code flow, then waits for the resulting
 * per-user connection.
 */
export function SignInCard(props: SignInCardProps) {
  if (props.entry.credentialEnrollment?.method === 'oauth_authorization_code') {
    return <OAuthAuthorizationCard {...props} />;
  }
  return <EnrollmentSignInCard {...props} />;
}

function inputType(type: string): string {
  if (type === 'password') return 'password';
  if (type === 'url') return 'url';
  return 'text';
}

function OAuthAuthorizationCard({
  entry,
  connection,
  headless = false,
  credentialName: requestedName,
  disabled = false,
  oauthApp = '',
  testResult,
  testing = false,
  onTest,
}: SignInCardProps) {
  const [config, setConfig] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const authorize = useStartOAuthAuthorization();
  const queryClient = useQueryClient();
  const missingConfig = missingConfigKeys(entry, config);

  useEffect(() => {
    if (!waiting || connection) return;
    const interval = window.setInterval(() => {
      void queryClient.invalidateQueries({ queryKey: setupKeys.integrations });
    }, ENROLLMENT_POLL_MS);
    return () => window.clearInterval(interval);
  }, [connection, queryClient, waiting]);

  const begin = () => {
    setTouched(true);
    if (missingConfig.length > 0) return;
    const credentialName =
      requestedName ??
      connection?.credentialName ??
      entry.credentialEnrollment?.defaultCredentialName ??
      credentialNameFor(entry.slug);
    const popup = window.open('', `${entry.slug}-oauth`, 'popup,width=720,height=840');
    authorize.mutate(
      {
        slug: entry.slug,
        credentialName,
        oauthApp,
        config: buildConfigPayload(entry, config),
      },
      {
        onSuccess: ({ url }) => {
          setWaiting(true);
          if (popup) popup.location.href = url;
          else window.open(url, `${entry.slug}-oauth`, 'popup,width=720,height=840');
        },
        onError: () => popup?.close(),
      },
    );
  };

  const connected = connection !== undefined && !connectionNeedsSignIn(connection);
  const Wrapper = headless ? 'div' : 'section';
  return (
    <Wrapper
      className={headless ? 'setup-col' : 'setup-card'}
      data-testid={`setup-integration-${entry.slug}`}
    >
      {Object.entries(entry.configSchema.properties ?? {}).map(([key, schema]) => (
        <Field
          key={key}
          label={schema.label}
          required={(entry.configSchema.required ?? []).includes(key)}
          hint={
            [schema.description, schema.type === 'string[]' ? 'Comma-separated.' : '']
              .filter(Boolean)
              .join(' ') || undefined
          }
          error={touched && missingConfig.includes(key) ? `${schema.label} is required` : undefined}
        >
          <Input
            type={inputType(schema.type)}
            placeholder={schema.default ?? ''}
            value={config[key] ?? ''}
            onChange={(event) =>
              setConfig((current) => ({ ...current, [key]: event.target.value }))
            }
            data-testid={`setup-config-${entry.slug}-${key}`}
          />
        </Field>
      ))}
      <div className="setup-form__actions">
        {connected ? (
          <>
            <span className="setup-note">
              <CheckIcon size={13} /> Signed in · credential {connection.credentialName}
            </span>
            {onTest ? (
              <button
                type="button"
                className="setup-btn"
                onClick={() => onTest(connection.id)}
                disabled={testing}
                data-testid={`setup-test-${entry.slug}`}
              >
                {testing ? 'Testing…' : 'Test connection'}
              </button>
            ) : null}
          </>
        ) : (
          <button
            type="button"
            className="setup-btn"
            onClick={begin}
            disabled={authorize.isPending || waiting || disabled}
            data-testid={`setup-signin-start-${entry.slug}`}
          >
            {authorize.isPending
              ? 'Opening…'
              : waiting
                ? 'Waiting for approval…'
                : `Sign in to ${entry.name}`}
          </button>
        )}
        {authorize.error ? (
          <span className="setup-error" role="alert">
            {errorMessage(authorize.error)}
          </span>
        ) : null}
        {waiting && !connected ? (
          <span className="setup-note">Finish approval in the provider window.</span>
        ) : null}
        {testResult ? <TestOutcome slug={entry.slug} result={testResult} /> : null}
      </div>
    </Wrapper>
  );
}

function EnrollmentSignInCard({
  entry,
  connection,
  headless = false,
  credentialName: requestedName,
  disabled = false,
  oauthApp,
  testResult,
  testing = false,
  onTest,
}: SignInCardProps) {
  const [enrollmentId, setEnrollmentId] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const start = useStartEnrollment();
  const cancel = useCancelEnrollment();
  const submit = useSubmitEnrollmentCode();
  const enrollmentQuery = useEnrollment(enrollmentId);
  const enrollment: Enrollment | undefined = enrollmentQuery.data ?? start.data ?? undefined;
  const active = isEnrollmentActive(enrollment);
  const error = start.error ?? cancel.error ?? submit.error ?? enrollmentQuery.error;

  const begin = () => {
    setCode('');
    // Re-use the connection a previous attempt created so a retry never
    // leaves a second, unusable connection behind.
    const credentialName =
      requestedName ??
      connection?.credentialName ??
      entry.credentialEnrollment?.defaultCredentialName ??
      credentialNameFor(entry.slug);
    start.mutate(
      { slug: entry.slug, credentialName, oauthApp },
      { onSuccess: (started) => setEnrollmentId(started.id) },
    );
  };

  const sendCode = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!enrollment || !code.trim()) return;
    submit.mutate({ enrollmentId: enrollment.id, code: code.trim() });
  };

  const signedIn = connection !== undefined && !connectionNeedsSignIn(connection);
  const connectedNow = signedIn || enrollment?.state === 'complete';

  const Wrapper = headless ? 'div' : 'section';
  return (
    <Wrapper
      className={headless ? 'setup-col' : 'setup-card'}
      data-testid={`setup-integration-${entry.slug}`}
    >
      {headless ? null : (
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">{entry.name}</h3>
            <p className="setup-card__desc">{entry.description}</p>
          </div>
          {connectedNow ? (
            <span className="setup-chip setup-chip--ok">
              <CheckIcon size={12} /> Connected
            </span>
          ) : active ? (
            <span className="setup-chip setup-chip--brand">Signing in…</span>
          ) : (
            <span className="setup-chip">Not connected</span>
          )}
        </div>
      )}

      {connectedNow ? (
        <div className="setup-form__actions" data-testid={`setup-signin-done-${entry.slug}`}>
          <span className="setup-note">
            <CheckIcon size={13} /> Signed in
            {connection ? ` · credential ${connection.credentialName}` : ''}
          </span>
          {connection && onTest ? (
            <button
              type="button"
              className="setup-btn"
              onClick={() => onTest(connection.id)}
              disabled={testing}
              data-testid={`setup-test-${entry.slug}`}
            >
              {testing ? 'Testing…' : 'Test connection'}
            </button>
          ) : null}
          {testResult ? <TestOutcome slug={entry.slug} result={testResult} /> : null}
        </div>
      ) : active && enrollment ? (
        <div className="setup-col" data-testid={`setup-signin-${entry.slug}`}>
          {enrollment.state === 'pending' || !enrollment.verificationUri ? (
            <div className="setup-note">
              Starting the sign-in helper… The first one on this host also pulls the session runtime
              image, which can take a few minutes.
            </div>
          ) : (
            <>
              <div className="setup-row">
                <span className="setup-row__icon">1</span>
                <div className="setup-row__body">
                  <span className="setup-row__title">Open the sign-in page</span>
                  <span className="setup-row__detail">
                    <a
                      href={enrollment.verificationUri}
                      target="_blank"
                      rel="noreferrer noopener"
                      data-testid={`setup-signin-link-${entry.slug}`}
                    >
                      {enrollment.verificationUri}
                    </a>
                  </span>
                </div>
              </div>
              {enrollment.userCode ? (
                <div className="setup-row">
                  <span className="setup-row__icon">2</span>
                  <div className="setup-row__body">
                    <span className="setup-row__title">Enter this code</span>
                    <span
                      className="setup-row__detail setup-code"
                      data-testid={`setup-signin-code-${entry.slug}`}
                    >
                      {enrollment.userCode}
                    </span>
                  </div>
                </div>
              ) : null}
              {enrollment.inputRequired ? (
                <form className="setup-form" onSubmit={sendCode}>
                  <Field
                    label="Paste the code the page shows you"
                    hint="Claude hands you a one-time authorization code after you approve."
                  >
                    <Input
                      autoComplete="off"
                      value={code}
                      onChange={(event) => setCode(event.target.value)}
                      data-testid={`setup-signin-input-${entry.slug}`}
                    />
                  </Field>
                  <div className="setup-form__actions">
                    <button
                      type="submit"
                      className="setup-btn"
                      disabled={submit.isPending || !code.trim()}
                      data-testid={`setup-signin-submit-${entry.slug}`}
                    >
                      {submit.isPending ? 'Checking…' : 'Finish sign-in'}
                    </button>
                  </div>
                </form>
              ) : (
                <div className="setup-note">
                  Waiting for the provider to confirm… this page updates by itself.
                </div>
              )}
            </>
          )}
          <div className="setup-form__actions">
            <button
              type="button"
              className="setup-btn setup-btn--ghost"
              onClick={() => cancel.mutate(enrollment.id)}
              disabled={cancel.isPending}
              data-testid={`setup-signin-cancel-${entry.slug}`}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="setup-form__actions">
          <button
            type="button"
            className="setup-btn"
            onClick={begin}
            disabled={start.isPending || disabled}
            data-testid={`setup-signin-start-${entry.slug}`}
          >
            {start.isPending ? 'Starting…' : `Sign in to ${entry.name}`}
          </button>
          {enrollment && !active ? (
            <span
              className="setup-note setup-note--warn"
              data-testid={`setup-signin-failed-${entry.slug}`}
            >
              <AlertIcon size={13} /> {enrollmentFailureMessage(enrollment)}
            </span>
          ) : connection ? (
            <span
              className="setup-note setup-note--warn"
              data-testid={`setup-signin-needed-${entry.slug}`}
            >
              <AlertIcon size={13} /> Sign-in needed
              {connection.credentialStatus === 'enrolling' ? ' (a sign-in is in progress)' : ''}
            </span>
          ) : null}
        </div>
      )}
      {error ? (
        <span className="setup-error" role="alert">
          {errorMessage(error)}
        </span>
      ) : null}
    </Wrapper>
  );
}
