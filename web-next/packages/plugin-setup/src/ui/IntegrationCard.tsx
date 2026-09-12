import { useState, type FormEvent } from 'react';
import { Field, Input } from '@niuulabs/ui';
import {
  buildConfigPayload,
  credentialNameFor,
  isConnectableFromWizard,
  missingCredentialKeys,
  type CatalogEntry,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
} from '../domain/setup';
import { CheckIcon, AlertIcon } from './icons';

export interface IntegrationCardProps {
  entry: CatalogEntry;
  connection: IntegrationConnection | undefined;
  connecting: boolean;
  connectError: Error | null;
  testResult: IntegrationTestResult | undefined;
  testing: boolean;
  onConnect: (input: ConnectIntegrationInput) => void;
  onTest: (connectionId: string) => void;
}

function inputType(type: string): string {
  if (type === 'password') return 'password';
  if (type === 'url') return 'url';
  return 'text';
}

/** One catalog entry: connected state, or a form built from its credential/config schema. */
export function IntegrationCard({
  entry,
  connection,
  connecting,
  connectError,
  testResult,
  testing,
  onConnect,
  onTest,
}: IntegrationCardProps) {
  const [credential, setCredential] = useState<Record<string, string>>({});
  const [config, setConfig] = useState<Record<string, string>>({});
  const [touched, setTouched] = useState(false);
  const connectable = isConnectableFromWizard(entry);
  const missing = missingCredentialKeys(entry, credential);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (missing.length > 0) return;
    onConnect({
      slug: entry.slug,
      credentialName: credentialNameFor(entry.slug),
      credential,
      config: buildConfigPayload(entry, config),
    });
  };

  return (
    <section className="setup-card" data-testid={`setup-integration-${entry.slug}`}>
      <div className="setup-card__head">
        <div>
          <h3 className="setup-card__title">{entry.name}</h3>
          <p className="setup-card__desc">{entry.description}</p>
        </div>
        {connection ? (
          <span className="setup-chip setup-chip--ok">
            <CheckIcon size={12} /> Connected
          </span>
        ) : connectable ? (
          <span className="setup-chip">Not connected</span>
        ) : (
          <span className="setup-chip setup-chip--warn">Needs interactive sign-in</span>
        )}
      </div>

      {connection ? (
        <div className="setup-form__actions">
          <span className="setup-note">credential {connection.credentialName}</span>
          <button
            type="button"
            className="setup-btn"
            onClick={() => onTest(connection.id)}
            disabled={testing}
            data-testid={`setup-test-${entry.slug}`}
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          {testResult ? (
            testResult.success ? (
              <span className="setup-note" data-testid={`setup-test-ok-${entry.slug}`}>
                <CheckIcon size={13} /> Works
                {testResult.workspace ? ` · ${testResult.workspace}` : ''}
                {testResult.user ? ` · ${testResult.user}` : ''}
              </span>
            ) : (
              <span
                className="setup-note setup-note--warn"
                data-testid={`setup-test-failed-${entry.slug}`}
              >
                <AlertIcon size={13} /> {testResult.error ?? 'Test failed'}
              </span>
            )
          ) : null}
        </div>
      ) : connectable ? (
        <form className="setup-form" onSubmit={submit} data-testid={`setup-form-${entry.slug}`}>
          {Object.entries(entry.credentialSchema.properties ?? {}).map(([key, schema]) => (
            <Field
              key={key}
              label={schema.label}
              required
              error={touched && missing.includes(key) ? `${schema.label} is required` : undefined}
            >
              <Input
                type={inputType(schema.type)}
                autoComplete="off"
                value={credential[key] ?? ''}
                onChange={(event) =>
                  setCredential((prev) => ({ ...prev, [key]: event.target.value }))
                }
                data-testid={`setup-input-${entry.slug}-${key}`}
              />
            </Field>
          ))}
          {Object.entries(entry.configSchema.properties ?? {}).map(([key, schema]) => (
            <Field
              key={key}
              label={schema.label}
              hint={schema.type === 'string[]' ? 'Comma-separated' : undefined}
            >
              <Input
                type={inputType(schema.type)}
                placeholder={schema.default ?? ''}
                value={config[key] ?? ''}
                onChange={(event) => setConfig((prev) => ({ ...prev, [key]: event.target.value }))}
                data-testid={`setup-config-${entry.slug}-${key}`}
              />
            </Field>
          ))}
          <div className="setup-form__actions">
            <button
              type="submit"
              className="setup-btn"
              disabled={connecting}
              data-testid={`setup-connect-${entry.slug}`}
            >
              {connecting ? 'Connecting…' : `Connect ${entry.name}`}
            </button>
            {connectError ? (
              <span className="setup-error" role="alert">
                {connectError.message}
              </span>
            ) : null}
          </div>
        </form>
      ) : (
        <p className="setup-note setup-note--warn">
          <AlertIcon size={13} /> This provider signs in through a browser or device flow, which
          this install cannot run yet. Use an API key provider for now.
        </p>
      )}
    </section>
  );
}
