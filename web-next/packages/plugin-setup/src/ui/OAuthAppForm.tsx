import { useState, type FormEvent } from 'react';
import { Field, Input } from '@niuulabs/ui';
import { oauthAppHelp, type CatalogEntry } from '../domain/setup';
import { useRegisterOAuthClient } from './hooks';

export interface OAuthAppFormProps {
  entry: CatalogEntry;
}

/**
 * Sign-in through GitHub or GitLab runs through an OAuth application the
 * person owns, never one someone else registered. The first time, this form
 * takes the application's client id; the platform keeps it and the sign-in
 * card takes over as soon as the catalog says the sign-in can run.
 */
export function OAuthAppForm({ entry }: OAuthAppFormProps) {
  const help = oauthAppHelp(entry.slug);
  const register = useRegisterOAuthClient();
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [touched, setTouched] = useState(false);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (!clientId.trim()) return;
    register.mutate({
      slug: entry.slug,
      input: { clientId: clientId.trim(), clientSecret: clientSecret.trim() },
    });
  };

  return (
    <div className="setup-pane" data-testid={`setup-oauth-app-${entry.slug}`}>
      <div className="setup-pane__intro">
        <p>
          Signing in to {entry.name} goes through an application you own, so nothing depends on
          anyone else. It takes a minute, once:
        </p>
        <ol className="setup-pane__steps">
          <li>
            <a href={help.createUrl} target="_blank" rel="noreferrer noopener">
              {help.createLabel}
            </a>
            . {help.createHint}
          </li>
          {help.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
          <li>Paste the {help.idLabel} below.</li>
        </ol>
      </div>
      <form className="setup-form" onSubmit={submit}>
        <Field
          label={help.idLabel}
          required
          error={touched && !clientId.trim() ? `${help.idLabel} is required` : undefined}
        >
          <Input
            autoComplete="off"
            value={clientId}
            onChange={(event) => setClientId(event.target.value)}
            data-testid={`setup-oauth-app-id-${entry.slug}`}
          />
        </Field>
        {help.secretHint ? (
          <Field label={help.secretLabel} hint={help.secretHint}>
            <Input
              type="password"
              autoComplete="off"
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
              data-testid={`setup-oauth-app-secret-${entry.slug}`}
            />
          </Field>
        ) : null}
        <div className="setup-form__actions">
          <button
            type="submit"
            className="setup-btn"
            disabled={register.isPending}
            data-testid={`setup-oauth-app-save-${entry.slug}`}
          >
            {register.isPending ? 'Saving…' : 'Save and sign in'}
          </button>
          {register.error ? (
            <span className="setup-error" role="alert">
              {register.error.message}
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}
