import { useEffect, useState } from 'react';
import type { ForgeProject, TeamCatalogEntry } from '@niuulabs/plugin-volundr';
import { useQueryClient } from '@tanstack/react-query';
import {
  PROVIDER_CONNECTED_KEY,
  errorDetail,
  teamInstallSignal,
  teamIntegrationsKey,
  useConnectServiceAccount,
  useStartTeamOAuth,
} from '../application/useProjectIntegrations';
import { BUTTON, CONTROL } from './ProjectMembers';

const POPUP_FEATURES = 'popup,width=720,height=840';

function fieldsOf(entry: TeamCatalogEntry) {
  const required = new Set(entry.config_schema.required ?? []);
  return Object.entries(entry.config_schema.properties ?? {}).map(([key, field]) => ({
    key,
    label: field.label ?? key,
    type: field.type === 'url' ? 'url' : 'text',
    required: required.has(key),
  }));
}

/** The form for one catalog entry: its config fields, and a service account's credentials. */
function ConnectForm({
  project,
  entry,
  onDone,
}: {
  project: ForgeProject;
  entry: TeamCatalogEntry;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const connect = useConnectServiceAccount(project);
  const authorize = useStartTeamOAuth(project);
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [config, setConfig] = useState<Record<string, string>>({});
  const [waiting, setWaiting] = useState(false);
  const fields = fieldsOf(entry);
  const serviceAccount = entry.team_install === 'client_credentials';
  const error = connect.error ?? authorize.error;
  const missing =
    fields.some((field) => field.required && !config[field.key]?.trim()) ||
    (serviceAccount && (!clientId.trim() || !clientSecret));

  useEffect(() => {
    if (!waiting) return;
    const signal = teamInstallSignal(project.id, entry.slug);
    const notified = (event: StorageEvent) => {
      if (event.key !== PROVIDER_CONNECTED_KEY || event.newValue !== signal) return;
      setWaiting(false);
      void queryClient.invalidateQueries({ queryKey: teamIntegrationsKey(project.id) });
      onDone();
    };
    window.addEventListener('storage', notified);
    return () => window.removeEventListener('storage', notified);
  }, [waiting, project.id, entry.slug, queryClient, onDone]);

  const submit = () => {
    const settings = Object.fromEntries(
      Object.entries(config).filter(([, value]) => value.trim() !== ''),
    );
    if (serviceAccount) {
      connect.mutate(
        {
          slug: entry.slug,
          credential: { client_id: clientId.trim(), client_secret: clientSecret },
          config: settings,
        },
        { onSuccess: onDone },
      );
      return;
    }
    // Opened now, while the click still counts, and pointed at the provider once Forge answers.
    const popup = window.open('', `${entry.slug}-team-oauth`, POPUP_FEATURES);
    authorize.mutate(
      { slug: entry.slug, config: settings },
      {
        onSuccess: ({ url }) => {
          setWaiting(true);
          if (popup) popup.location.href = url;
          else window.open(url, `${entry.slug}-team-oauth`, POPUP_FEATURES);
        },
        onError: () => popup?.close(),
      },
    );
  };

  return (
    <form
      className="niuu:flex niuu:flex-col niuu:gap-2"
      aria-label={`Connect ${entry.name}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (!missing) submit();
      }}
    >
      {serviceAccount ? (
        <>
          <label className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-secondary">
            Client ID *
            <input
              className={CONTROL}
              value={clientId}
              onChange={(event) => setClientId(event.target.value)}
              autoComplete="off"
            />
          </label>
          <label className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-secondary">
            Client secret *
            <input
              className={CONTROL}
              type="password"
              value={clientSecret}
              onChange={(event) => setClientSecret(event.target.value)}
              autoComplete="off"
            />
          </label>
        </>
      ) : null}
      {fields.map((field) => (
        <label
          key={field.key}
          className="niuu:flex niuu:flex-col niuu:gap-1 niuu:text-xs niuu:text-text-secondary"
        >
          {field.label}
          {field.required ? ' *' : ''}
          <input
            className={CONTROL}
            type={field.type}
            value={config[field.key] ?? ''}
            onChange={(event) =>
              setConfig((prev) => ({ ...prev, [field.key]: event.target.value }))
            }
          />
        </label>
      ))}
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="submit"
          className={BUTTON}
          disabled={missing || connect.isPending || authorize.isPending || waiting}
        >
          {serviceAccount ? 'Connect service account' : `Install ${entry.name}`}
        </button>
        {waiting ? (
          <span className="niuu:text-xs niuu:text-text-muted">
            Finish approval in the provider window.
          </span>
        ) : null}
      </div>
      {error ? (
        <span role="alert" className="niuu:text-xs niuu:text-critical-fg">
          {errorDetail(error)}
        </span>
      ) : null}
    </form>
  );
}

/** Pick an entry of the team catalog and connect it. Owners only. */
export function ConnectIntegration({
  project,
  catalog,
  onClose,
}: {
  project: ForgeProject;
  catalog: readonly TeamCatalogEntry[];
  onClose: () => void;
}) {
  const [chosen, setChosen] = useState<string | null>(null);
  const entry = catalog.find((candidate) => candidate.slug === chosen);

  return (
    <div
      className="niuu:flex niuu:flex-col niuu:gap-2 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3"
      data-testid="project-integration-connect"
    >
      <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
        {catalog.map((candidate) => (
          <button
            key={candidate.slug}
            type="button"
            className={BUTTON}
            aria-pressed={candidate.slug === chosen}
            onClick={() => setChosen(candidate.slug)}
          >
            {candidate.name}
          </button>
        ))}
        <button type="button" className={BUTTON} onClick={onClose}>
          Cancel
        </button>
      </div>
      {entry ? (
        <ConnectForm key={entry.slug} project={project} entry={entry} onDone={onClose} />
      ) : null}
    </div>
  );
}
