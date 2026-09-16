import { useState, type FormEvent } from 'react';
import { Field, Input } from '@niuulabs/ui';
import {
  oauthAppHelp,
  oauthAppKey,
  type CatalogEntry,
  type OAuthApp,
  errorMessage,
} from '../domain/setup';
import { useRegisterOAuthClient } from './hooks';

export interface OAuthAppFormProps {
  entry: CatalogEntry;
  /** Applications already registered for this provider; a new one needs its own name. */
  existingApps?: OAuthApp[];
  /** Called with the new application's key once the platform has stored it. */
  onRegistered?: (app: string) => void;
}

/**
 * Provider sign-in runs through an OAuth application the
 * person owns, never one someone else registered. This form takes the
 * application's client id; the platform keeps it and the sign-in card takes
 * over. A provider can have several applications, one per account, so once
 * one exists the next one gets a name of its own.
 */
export function OAuthAppForm({ entry, existingApps = [], onRegistered }: OAuthAppFormProps) {
  const help = oauthAppHelp(entry.slug);
  const register = useRegisterOAuthClient();
  const [appName, setAppName] = useState('');
  const [clientId, setClientId] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const defaultHost = help.createUrl ? new URL(help.createUrl).origin : '';
  let createUrl = help.createUrl;
  try {
    const host = new URL(baseUrl || defaultHost);
    if (help.supportsCustomHost && ['https:', 'http:'].includes(host.protocol)) {
      createUrl = new URL(new URL(help.createUrl).pathname, host.origin).href;
    }
  } catch {
    /* The URL input reports invalid hosts on submit. */
  }
  const [clientSecret, setClientSecret] = useState('');
  const [touched, setTouched] = useState(false);
  const needsName = existingApps.length > 0;
  const appKey = oauthAppKey(appName);
  const nameTaken = appName.trim() !== '' && existingApps.some((app) => app.app === appKey);
  const nameMissing = needsName && !appName.trim();
  const secretMissing = entry.oauthClientSecretRequired === true && !clientSecret.trim();
  const callbackUrl =
    typeof window === 'undefined'
      ? '/api/v1/integrations/oauth/callback'
      : `${window.location.origin}/api/v1/integrations/oauth/callback`;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (!clientId.trim() || secretMissing || nameMissing || nameTaken) return;
    register.mutate(
      {
        slug: entry.slug,
        input: {
          app: appKey,
          clientId: clientId.trim(),
          clientSecret: clientSecret.trim(),
          baseUrl: baseUrl.trim(),
        },
      },
      { onSuccess: () => onRegistered?.(appKey) },
    );
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
            <a href={createUrl} target="_blank" rel="noreferrer noopener">
              {help.createLabel}
            </a>
            . {help.createHint}
          </li>
          {help.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
          {entry.oauthScopes && entry.oauthScopes.length > 0 ? (
            <li>
              Add these scopes: <code>{entry.oauthScopes.join(' ')}</code>.
            </li>
          ) : null}
          {help.usesCallback ? (
            <li>
              Set the callback URL to <code>{callbackUrl}</code>.
            </li>
          ) : null}
          <li>Paste the {help.idLabel} below.</li>
        </ol>
      </div>
      <form className="setup-form" onSubmit={submit}>
        {help.supportsCustomHost ? (
          <Field
            label="Git host URL"
            hint="Use your self-hosted GitLab or GitHub Enterprise address."
          >
            <Input
              type="url"
              value={baseUrl}
              placeholder={defaultHost}
              onChange={(event) => setBaseUrl(event.target.value)}
              data-testid={`setup-oauth-app-host-${entry.slug}`}
            />
          </Field>
        ) : null}
        {needsName ? (
          <Field
            label="Name for this application"
            required
            hint={`Already registered: ${existingApps.map((app) => app.app).join(', ')}. Name this one after the organisation or account it belongs to.`}
            error={
              touched && nameMissing
                ? 'A name is required'
                : nameTaken
                  ? 'That name is already in use.'
                  : undefined
            }
          >
            <Input
              autoComplete="off"
              value={appName}
              onChange={(event) => setAppName(event.target.value)}
              data-testid={`setup-oauth-app-name-${entry.slug}`}
            />
          </Field>
        ) : null}
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
        {help.secretHint || entry.oauthClientSecretRequired ? (
          <Field
            label={help.secretLabel}
            hint={help.secretHint}
            required={entry.oauthClientSecretRequired}
            error={touched && secretMissing ? `${help.secretLabel} is required` : undefined}
          >
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
            disabled={register.isPending || nameTaken}
            data-testid={`setup-oauth-app-save-${entry.slug}`}
          >
            {register.isPending ? 'Saving…' : 'Save and sign in'}
          </button>
          {register.error ? (
            <span className="setup-error" role="alert">
              {errorMessage(register.error)}
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}
