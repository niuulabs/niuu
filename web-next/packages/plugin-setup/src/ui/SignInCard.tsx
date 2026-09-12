import { useState, type FormEvent } from 'react';
import { Field, Input } from '@niuulabs/ui';
import {
  connectionNeedsSignIn,
  credentialNameFor,
  enrollmentFailureMessage,
  isEnrollmentActive,
  type CatalogEntry,
  type Enrollment,
  type IntegrationConnection,
} from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';
import {
  useCancelEnrollment,
  useEnrollment,
  useStartEnrollment,
  useSubmitEnrollmentCode,
} from './hooks';

export interface SignInCardProps {
  entry: CatalogEntry;
  connection: IntegrationConnection | undefined;
}

/**
 * A catalog entry that is connected by signing in through the provider.
 *
 * The platform runs the official CLI in a sealed helper; the wizard shows the
 * link (and device code) it produces, polls until the provider confirms, and
 * for Claude passes the authorization code the browser hands back.
 */
export function SignInCard({ entry, connection }: SignInCardProps) {
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
    const credentialName = connection?.credentialName ?? credentialNameFor(entry.slug);
    start.mutate(
      { slug: entry.slug, credentialName },
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

  return (
    <section className="setup-card" data-testid={`setup-integration-${entry.slug}`}>
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

      {connectedNow ? (
        <div className="setup-note" data-testid={`setup-signin-done-${entry.slug}`}>
          <CheckIcon size={13} /> Signed in
          {connection ? ` · credential ${connection.credentialName}` : ''}
        </div>
      ) : active && enrollment ? (
        <div className="setup-col" data-testid={`setup-signin-${entry.slug}`}>
          {enrollment.state === 'pending' || !enrollment.verificationUri ? (
            <div className="setup-note">Starting the sign-in helper…</div>
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
            disabled={start.isPending}
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
          {error.message}
        </span>
      ) : null}
    </section>
  );
}
