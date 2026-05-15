import { useEffect, useMemo, useState } from 'react';
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useParams, useRouter } from '@tanstack/react-router';
import { createApiClient } from '@niuulabs/query';
import { cn } from '@niuulabs/ui';
import {
  useMountedSettingsProviders,
  type MountedSettingsProvider,
  type RemoteSettingsField,
  type RemoteSettingsIntegrationsResource,
  type RemoteSettingsProviderSchema,
  type RemoteSettingsResource,
  type RemoteSettingsSectionSchema,
  type RemoteSettingsTokensResource,
} from './SettingsRegistry';
import './SettingsPage.css';

function isRemoteProvider(
  provider: MountedSettingsProvider,
): provider is Extract<MountedSettingsProvider, { source: 'remote' }> {
  return provider.source === 'remote';
}

function providerPath(providerId: string, sectionId?: string): string {
  return sectionId ? `/settings/${providerId}/${sectionId}` : `/settings/${providerId}`;
}

function buildInitialDraft(section: RemoteSettingsSectionSchema | null): Record<string, unknown> {
  if (!section) return {};
  return Object.fromEntries(section.fields.map((field) => [field.key, field.value]));
}

type ProviderStatus = 'ready' | 'loading' | 'missing' | 'error';

interface PersonalAccessTokenRecord {
  id: string;
  name: string;
  createdAt: string;
  lastUsedAt: string | null;
}

interface CreatePersonalAccessTokenResult extends PersonalAccessTokenRecord {
  token: string;
}

interface ResourceSchemaProperty {
  label?: string;
  type?: string;
  description?: string;
  default?: unknown;
}

interface IntegrationCatalogSchema {
  required?: string[];
  properties?: Record<string, ResourceSchemaProperty>;
}

interface IntegrationCatalogEntry {
  id: string;
  slug?: string;
  name: string;
  description?: string;
  integration_type?: string;
  integrationType?: string;
  adapter?: string;
  auth_type?: string;
  authType?: string;
  credential_schema?: IntegrationCatalogSchema;
  credentialSchema?: IntegrationCatalogSchema;
  config_schema?: IntegrationCatalogSchema;
  configSchema?: IntegrationCatalogSchema;
}

interface IntegrationConnectionRecord {
  id: string;
  slug?: string;
  integrationType?: string;
  integration_type?: string;
  credentialName?: string;
  credential_name?: string;
  enabled?: boolean;
  createdAt?: string;
  created_at?: string;
  updatedAt?: string;
  updated_at?: string;
}

interface NormalizedSettingsSection {
  id: string;
  label: string;
  description?: string;
  path?: string;
  saveLabel?: string;
  fields: RemoteSettingsField[];
  resources: RemoteSettingsResource[];
  writable: boolean;
}

interface ProviderSnapshot {
  provider: MountedSettingsProvider;
  title: string;
  subtitle?: string;
  sections: NormalizedSettingsSection[];
  status: ProviderStatus;
  scopeLabel: string;
}

function describeScope(scope: string): string {
  return scope === 'user' ? 'personal settings' : 'service settings';
}

function formatFieldValue(field: RemoteSettingsField, value: unknown): string {
  if (field.type === 'boolean') {
    return value ? 'Enabled' : 'Disabled';
  }
  if (field.type === 'select') {
    const option = field.options?.find((entry) => entry.value === value);
    if (option) return option.label;
  }
  if (value == null || value === '') return '—';
  return String(value);
}

function normalizeSections(schema: RemoteSettingsProviderSchema | null): NormalizedSettingsSection[] {
  return (schema?.sections ?? []).map((section) => ({
    ...section,
    resources: section.resources ?? [],
    writable:
      section.fields.some((field) => !field.readOnly) ||
      (section.resources ?? []).some((resource) => resource.writable !== false),
  }));
}

function resolveRootBase(baseUrl: string): string {
  if (/^https?:\/\//.test(baseUrl)) {
    return new URL(baseUrl).origin;
  }
  if (typeof window !== 'undefined' && window.location.origin) {
    return window.location.origin;
  }
  return 'http://localhost';
}

function normalizeTokenRow(token: PersonalAccessTokenRecord | CreatePersonalAccessTokenResult) {
  return {
    id: token.id,
    name: token.name,
    createdAt: (token as { createdAt?: string; created_at?: string }).createdAt ??
      (token as { createdAt?: string; created_at?: string }).created_at ??
      '',
    lastUsedAt:
      (token as { lastUsedAt?: string | null; last_used_at?: string | null }).lastUsedAt ??
      (token as { lastUsedAt?: string | null; last_used_at?: string | null }).last_used_at ??
      null,
    token: (token as { token?: string }).token,
  };
}

function normalizeCatalogSchema(
  schema: IntegrationCatalogSchema | undefined,
): IntegrationCatalogSchema {
  return {
    required: schema?.required ?? [],
    properties: schema?.properties ?? {},
  };
}

function inferSecretType(entry: IntegrationCatalogEntry): 'api_key' | 'oauth_token' | 'generic' {
  const authType = entry.authType ?? entry.auth_type;
  if (authType === 'oauth2_authorization_code' || authType === 'oauth_token') {
    return 'oauth_token';
  }
  return 'api_key';
}

function formatTimestamp(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function buildInitialResourceValues(schema: IntegrationCatalogSchema | undefined): Record<string, string> {
  return Object.fromEntries(
    Object.entries(schema?.properties ?? {}).map(([key, property]) => {
      const defaultValue = property.default;
      if (Array.isArray(defaultValue)) {
        return [key, defaultValue.join('\n')];
      }
      return [key, defaultValue == null ? '' : String(defaultValue)];
    }),
  );
}

function normalizeIntegrationRecord(integration: IntegrationConnectionRecord) {
  return {
    id: integration.id,
    slug: integration.slug,
    integrationType: integration.integrationType ?? integration.integration_type ?? 'integration',
    credentialName: integration.credentialName ?? integration.credential_name ?? '',
    enabled: integration.enabled !== false,
    createdAt: integration.createdAt ?? integration.created_at ?? '',
    updatedAt: integration.updatedAt ?? integration.updated_at ?? '',
  };
}

function formatIntegrationType(value: string): string {
  return value.replace(/_/g, ' ');
}

function SettingsSidebar({
  snapshots,
  activeProviderId,
  activeSectionId,
}: {
  snapshots: ProviderSnapshot[];
  activeProviderId?: string;
  activeSectionId?: string;
}) {
  const router = useRouter();
  const totalSections = snapshots.reduce((sum, snapshot) => sum + snapshot.sections.length, 0);

  return (
    <aside className="settings-shell__sidebar">
      <div className="settings-shell__sidebar-header">
        <div className="settings-shell__sidebar-heading-row">
          <span className="settings-shell__sidebar-eyebrow">Settings</span>
          <span className="settings-shell__sidebar-count">{totalSections}</span>
        </div>
      </div>

      <div className="settings-shell__provider-groups">
        {snapshots.map((snapshot) => {
          const providerActive = snapshot.provider.id === activeProviderId;
          const targetPath = providerPath(snapshot.provider.id, snapshot.sections[0]?.id);
          return (
            <section
              key={snapshot.provider.id}
              className={cn(
                'settings-shell__provider-group',
                providerActive && 'settings-shell__provider-group--active',
              )}
            >
              <button
                type="button"
                onClick={() => {
                  void router.navigate({ to: targetPath as never });
                }}
                className="settings-shell__provider-header"
              >
                <span className="settings-shell__provider-title">{snapshot.title}</span>
                <span className="settings-shell__provider-count">{snapshot.sections.length}</span>
              </button>

              {snapshot.sections.length > 0 ? (
                <div className="settings-shell__provider-sections">
                  {snapshot.sections.map((section) => {
                    const sectionActive = providerActive && section.id === activeSectionId;
                    return (
                      <button
                        key={section.id}
                        type="button"
                        onClick={() => {
                          void router.navigate({
                            to: providerPath(snapshot.provider.id, section.id) as never,
                          });
                        }}
                        className={cn(
                          'settings-shell__section-link',
                          sectionActive && 'settings-shell__section-link--active',
                        )}
                        aria-current={sectionActive ? 'page' : undefined}
                      >
                        <span className="settings-shell__section-link-mark">◇</span>
                        <span className="settings-shell__section-link-label">{section.label}</span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="settings-shell__provider-empty">
                  {snapshot.status === 'loading'
                    ? 'Loading schema…'
                    : snapshot.status === 'missing'
                      ? 'Not mounted'
                      : snapshot.status === 'error'
                        ? 'Schema error'
                        : 'No sections'}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </aside>
  );
}

function SettingsField({
  field,
  value,
  onChange,
}: {
  field: RemoteSettingsField;
  value: unknown;
  onChange: (nextValue: unknown) => void;
}) {
  const description = field.description ? (
    <span className="settings-field__description">{field.description}</span>
  ) : null;
  const readOnly = field.readOnly === true;

  if (field.type === 'boolean' && !readOnly) {
    const checked = Boolean(value);
    return (
      <label className="settings-field settings-field--toggle">
        <div className="settings-field__meta">
          <span className="settings-field__label">{field.label}</span>
          {description}
        </div>
        <span className="settings-checkbox">
          <input
            type="checkbox"
            checked={checked}
            onChange={(event) => onChange(event.target.checked)}
            className="settings-checkbox__input"
          />
          <span className="settings-checkbox__ui">
            <span
              className={cn('settings-checkbox__box', checked && 'settings-checkbox__box--checked')}
              aria-hidden="true"
            >
              {checked ? '✓' : ''}
            </span>
            <span className="settings-checkbox__label" aria-hidden="true">
              {checked ? 'Enabled' : 'Disabled'}
            </span>
          </span>
        </span>
      </label>
    );
  }

  if (readOnly) {
    return (
      <div className="settings-field settings-field--readonly">
        <div className="settings-field__meta">
          <span className="settings-field__label">{field.label}</span>
          {description}
        </div>
        <div className="settings-field__value">{formatFieldValue(field, value)}</div>
      </div>
    );
  }

  if (field.type === 'textarea') {
    return (
      <label className="settings-field settings-field--stacked">
        <div className="settings-field__meta">
          <span className="settings-field__label">{field.label}</span>
          {description}
        </div>
        <textarea
          value={String(value ?? '')}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
          className="settings-field__textarea"
        />
      </label>
    );
  }

  if (field.type === 'select') {
    return (
      <label className="settings-field settings-field--editable">
        <div className="settings-field__meta">
          <span className="settings-field__label">{field.label}</span>
          {description}
        </div>
        <div className="settings-field__control-wrap">
          <select
            value={String(value ?? '')}
            onChange={(event) => onChange(event.target.value)}
            className="settings-field__control"
          >
            {(field.options ?? []).map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <span className="settings-field__select-caret" aria-hidden="true">
            ▾
          </span>
        </div>
      </label>
    );
  }

  return (
    <label className="settings-field settings-field--editable">
      <div className="settings-field__meta">
        <span className="settings-field__label">{field.label}</span>
        {description}
      </div>
      <input
        type={field.secret ? 'password' : field.type === 'number' ? 'number' : 'text'}
        value={String(value ?? '')}
        placeholder={field.placeholder}
        onChange={(event) => {
          const nextValue =
            field.type === 'number' ? Number(event.target.value || 0) : event.target.value;
          onChange(nextValue);
        }}
        className="settings-field__control"
      />
    </label>
  );
}

function TokensResourceCard({
  resource,
  rootBase,
  providerId,
}: {
  resource: RemoteSettingsTokensResource;
  rootBase: string;
  providerId: string;
}) {
  const client = useMemo(() => createApiClient(rootBase), [rootBase]);
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [createdToken, setCreatedToken] = useState<CreatePersonalAccessTokenResult | null>(null);

  const tokensQuery = useQuery({
    queryKey: ['settings-resource', providerId, resource.id, 'tokens'],
    queryFn: async () => {
      const rows = await client.get<Array<PersonalAccessTokenRecord | CreatePersonalAccessTokenResult>>(
        resource.listPath,
      );
      return rows.map(normalizeTokenRow);
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      return client.post<CreatePersonalAccessTokenResult>(resource.createPath, { name });
    },
    onSuccess: async (payload) => {
      setCreatedToken(normalizeTokenRow(payload) as CreatePersonalAccessTokenResult);
      setName('');
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'tokens'],
      });
    },
  });

  const revokeMutation = useMutation({
    mutationFn: async (id: string) => {
      return client.delete<void>(resource.deletePath.replace('{id}', id));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'tokens'],
      });
    },
  });

  return (
    <section className="settings-resource">
      <div className="settings-resource__header">
        <div>
          <h2 className="settings-resource__title">{resource.label}</h2>
          {resource.description ? (
            <p className="settings-resource__copy">{resource.description}</p>
          ) : null}
        </div>
      </div>

      <form
        className="settings-resource__composer"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim()) return;
          void createMutation.mutateAsync();
        }}
      >
        <label className="settings-resource__composer-field">
          <span className="settings-resource__composer-label">Token name</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. ci-runner or local-tools"
            className="settings-field__control"
          />
        </label>
        <button
          type="submit"
          className="settings-shell__save-button settings-shell__save-button--secondary"
          disabled={createMutation.isPending || !name.trim()}
        >
          {createMutation.isPending ? 'Creating…' : 'Create token'}
        </button>
      </form>

      {createdToken ? (
        <div className="settings-resource__callout">
          <div className="settings-resource__callout-title">New token</div>
          <code className="settings-resource__secret">{createdToken.token}</code>
          <p className="settings-resource__copy">
            This value is only shown once. Save it before closing this page.
          </p>
        </div>
      ) : null}

      <div className="settings-resource__list">
        {tokensQuery.isLoading ? (
          <div className="settings-resource__empty">Loading tokens…</div>
        ) : tokensQuery.isError ? (
          <div className="settings-resource__empty">Could not load tokens.</div>
        ) : tokensQuery.data && tokensQuery.data.length > 0 ? (
          tokensQuery.data.map((token) => (
            <div key={token.id} className="settings-resource__row">
              <div className="settings-resource__row-main">
                <div className="settings-resource__row-title">{token.name}</div>
                <div className="settings-resource__row-meta">
                  created {formatTimestamp(token.createdAt)} · last used{' '}
                  {token.lastUsedAt ? formatTimestamp(token.lastUsedAt) : 'never'}
                </div>
              </div>
              <button
                type="button"
                className="settings-resource__row-action"
                disabled={revokeMutation.isPending}
                onClick={() => {
                  void revokeMutation.mutateAsync(token.id);
                }}
              >
                Revoke
              </button>
            </div>
          ))
        ) : (
          <div className="settings-resource__empty">No personal access tokens yet.</div>
        )}
      </div>
    </section>
  );
}

function IntegrationSchemaFields({
  label,
  schema,
  values,
  onChange,
}: {
  label: string;
  schema: IntegrationCatalogSchema;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  const entries = Object.entries(schema.properties ?? {});
  if (entries.length === 0) return null;

  return (
    <div className="settings-resource__schema-group">
      <div className="settings-resource__group-label">{label}</div>
      <div className="settings-resource__schema-fields">
        {entries.map(([key, property]) => {
          const multiline = property.type === 'string[]' || property.type === 'textarea';
          return (
            <label
              key={key}
              className={cn(
                'settings-resource__composer-field',
                multiline && 'settings-resource__composer-field--wide',
              )}
            >
              <span className="settings-resource__composer-label">
                {property.label ?? key}
                {(schema.required ?? []).includes(key) ? (
                  <span className="settings-resource__required">required</span>
                ) : null}
              </span>
              {property.description ? (
                <span className="settings-field__description">{property.description}</span>
              ) : null}
              {multiline ? (
                <textarea
                  value={values[key] ?? ''}
                  onChange={(event) => onChange(key, event.target.value)}
                  className="settings-field__textarea"
                />
              ) : (
                <input
                  type={property.type === 'password' ? 'password' : 'text'}
                  value={values[key] ?? ''}
                  onChange={(event) => onChange(key, event.target.value)}
                  className="settings-field__control"
                />
              )}
            </label>
          );
        })}
      </div>
    </div>
  );
}

function IntegrationsResourceCard({
  resource,
  rootBase,
  providerId,
}: {
  resource: RemoteSettingsIntegrationsResource;
  rootBase: string;
  providerId: string;
}) {
  const client = useMemo(() => createApiClient(rootBase), [rootBase]);
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState('');
  const [credentialName, setCredentialName] = useState('');
  const [createCredential, setCreateCredential] = useState(true);
  const [credentialValues, setCredentialValues] = useState<Record<string, string>>({});
  const [configValues, setConfigValues] = useState<Record<string, string>>({});

  const catalogQuery = useQuery({
    queryKey: ['settings-resource', providerId, resource.id, 'integration-catalog'],
    queryFn: () => client.get<IntegrationCatalogEntry[]>(resource.catalogPath),
  });

  const integrationsQuery = useQuery({
    queryKey: ['settings-resource', providerId, resource.id, 'integrations'],
    queryFn: async () => {
      const rows = await client.get<IntegrationConnectionRecord[]>(resource.listPath);
      return rows.map(normalizeIntegrationRecord);
    },
  });

  const selectedEntry = useMemo(() => {
    const entries = catalogQuery.data ?? [];
    return entries.find((entry) => (entry.slug ?? entry.id) === selectedId) ?? entries[0] ?? null;
  }, [catalogQuery.data, selectedId]);

  const credentialSchema = useMemo(
    () => normalizeCatalogSchema(selectedEntry?.credentialSchema ?? selectedEntry?.credential_schema),
    [selectedEntry],
  );
  const configSchema = useMemo(
    () => normalizeCatalogSchema(selectedEntry?.configSchema ?? selectedEntry?.config_schema),
    [selectedEntry],
  );

  useEffect(() => {
    if (!selectedEntry) return;
    const nextId = selectedEntry.slug ?? selectedEntry.id;
    setSelectedId(nextId);
    setCredentialName((current) => current || `${nextId}-credential`);
    setCredentialValues(buildInitialResourceValues(credentialSchema));
    setConfigValues(buildInitialResourceValues(configSchema));
  }, [selectedEntry, credentialSchema, configSchema]);

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedEntry) {
        throw new Error('No integration selected');
      }
      if (createCredential) {
        const credentialPayload = Object.fromEntries(
          Object.entries(credentialValues).filter(([, value]) => value.trim() !== ''),
        );
        await client.post(resource.credentialCreatePath, {
          name: credentialName,
          secret_type: inferSecretType(selectedEntry),
          data: credentialPayload,
        });
      }

      const configPayload = Object.fromEntries(
        Object.entries(configValues)
          .map(([key, value]) => {
            const property = configSchema.properties?.[key];
            if (property?.type === 'string[]') {
              const items = value
                .split('\n')
                .map((entry) => entry.trim())
                .filter(Boolean);
              return [key, items];
            }
            return [key, value.trim()];
          })
          .filter(([, value]) => {
            if (Array.isArray(value)) return value.length > 0;
            return value !== '';
          }),
      );

      return client.post(resource.createPath, {
        integration_type: selectedEntry.integrationType ?? selectedEntry.integration_type ?? '',
        adapter: selectedEntry.adapter ?? '',
        credential_name: credentialName,
        config: configPayload,
        enabled: true,
        slug: selectedEntry.slug ?? selectedEntry.id,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'integrations'],
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      return client.delete<void>(resource.deletePath.replace('{id}', id));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'integrations'],
      });
    },
  });

  return (
    <section className="settings-resource">
      <div className="settings-resource__header">
        <div>
          <h2 className="settings-resource__title">{resource.label}</h2>
          {resource.description ? (
            <p className="settings-resource__copy">{resource.description}</p>
          ) : null}
        </div>
      </div>

      <form
        className="settings-resource__composer settings-resource__composer--stacked"
        onSubmit={(event) => {
          event.preventDefault();
          if (!selectedEntry || !credentialName.trim()) return;
          void createMutation.mutateAsync();
        }}
      >
        <div className="settings-resource__schema-fields">
          <label className="settings-resource__composer-field">
            <span className="settings-resource__composer-label">Integration</span>
            <div className="settings-field__control-wrap">
              <select
                value={selectedId}
                onChange={(event) => {
                  setSelectedId(event.target.value);
                  const next = (catalogQuery.data ?? []).find(
                    (entry) => (entry.slug ?? entry.id) === event.target.value,
                  );
                  setCredentialName(`${event.target.value}-credential`);
                  setCredentialValues(
                    buildInitialResourceValues(
                      normalizeCatalogSchema(next?.credentialSchema ?? next?.credential_schema),
                    ),
                  );
                  setConfigValues(
                    buildInitialResourceValues(
                      normalizeCatalogSchema(next?.configSchema ?? next?.config_schema),
                    ),
                  );
                }}
                className="settings-field__control"
              >
                {(catalogQuery.data ?? []).map((entry) => {
                  const key = entry.slug ?? entry.id;
                  return (
                    <option key={key} value={key}>
                      {entry.name}
                    </option>
                  );
                })}
              </select>
              <span className="settings-field__select-caret" aria-hidden="true">
                ▾
              </span>
            </div>
          </label>

          <label className="settings-resource__composer-field">
            <span className="settings-resource__composer-label">Credential name</span>
            <input
              value={credentialName}
              onChange={(event) => setCredentialName(event.target.value)}
              className="settings-field__control"
            />
          </label>
        </div>

        <label className="settings-field settings-field--toggle settings-resource__toggle">
          <div className="settings-field__meta">
            <span className="settings-field__label">Store credential first</span>
            <span className="settings-field__description">
              Disable this if you want to connect an existing credential by name.
            </span>
          </div>
          <span className="settings-checkbox">
            <input
              type="checkbox"
              checked={createCredential}
              onChange={(event) => setCreateCredential(event.target.checked)}
              className="settings-checkbox__input"
            />
            <span className="settings-checkbox__ui">
              <span
                className={cn(
                  'settings-checkbox__box',
                  createCredential && 'settings-checkbox__box--checked',
                )}
                aria-hidden="true"
              >
                {createCredential ? '✓' : ''}
              </span>
              <span className="settings-checkbox__label" aria-hidden="true">
                {createCredential ? 'Enabled' : 'Disabled'}
              </span>
            </span>
          </span>
        </label>

        {createCredential ? (
          <IntegrationSchemaFields
            label="Credential"
            schema={credentialSchema}
            values={credentialValues}
            onChange={(key, value) => {
              setCredentialValues((current) => ({ ...current, [key]: value }));
            }}
          />
        ) : null}

        <IntegrationSchemaFields
          label="Connection config"
          schema={configSchema}
          values={configValues}
          onChange={(key, value) => {
            setConfigValues((current) => ({ ...current, [key]: value }));
          }}
        />

        <div className="settings-resource__actions">
          {createMutation.isError ? (
            <span className="settings-shell__status settings-shell__status--error">
              Could not create this integration.
            </span>
          ) : null}
          {createMutation.isSuccess ? (
            <span className="settings-shell__status settings-shell__status--success">
              Integration connected.
            </span>
          ) : null}
          <button
            type="submit"
            className="settings-shell__save-button settings-shell__save-button--secondary"
            disabled={createMutation.isPending || !selectedEntry || !credentialName.trim()}
          >
            {createMutation.isPending ? 'Connecting…' : 'Add integration'}
          </button>
        </div>
      </form>

      <div className="settings-resource__list">
        {integrationsQuery.isLoading ? (
          <div className="settings-resource__empty">Loading integrations…</div>
        ) : integrationsQuery.isError ? (
          <div className="settings-resource__empty">Could not load integrations.</div>
        ) : integrationsQuery.data && integrationsQuery.data.length > 0 ? (
          integrationsQuery.data.map((integration) => (
            <div key={integration.id} className="settings-resource__row">
              <div className="settings-resource__row-main">
                <div className="settings-resource__row-title">
                  {(integration.slug ?? integration.integrationType).replace(/_/g, ' ')}
                </div>
                <div className="settings-resource__row-meta">
                  {formatIntegrationType(integration.integrationType)} · {integration.credentialName}{' '}
                  · {integration.enabled ? 'enabled' : 'disabled'}
                </div>
              </div>
              <button
                type="button"
                className="settings-resource__row-action"
                disabled={deleteMutation.isPending}
                onClick={() => {
                  void deleteMutation.mutateAsync(integration.id);
                }}
              >
                Disconnect
              </button>
            </div>
          ))
        ) : (
          <div className="settings-resource__empty">No integrations connected yet.</div>
        )}
      </div>
    </section>
  );
}

function SettingsSectionResources({
  snapshot,
  resources,
}: {
  snapshot: ProviderSnapshot;
  resources: RemoteSettingsResource[];
}) {
  const remoteProvider = isRemoteProvider(snapshot.provider) ? snapshot.provider : null;
  const rootBase = useMemo(
    () => (remoteProvider?.baseUrl ? resolveRootBase(remoteProvider.baseUrl) : null),
    [remoteProvider?.baseUrl],
  );

  if (!rootBase || resources.length === 0) return null;

  return (
    <div className="settings-shell__resources">
      {resources.map((resource) => {
        if (resource.type === 'tokens') {
          return (
            <TokensResourceCard
              key={resource.id}
              resource={resource}
              rootBase={rootBase}
              providerId={snapshot.provider.id}
            />
          );
        }
        if (resource.type === 'integrations') {
          return (
            <IntegrationsResourceCard
              key={resource.id}
              resource={resource}
              rootBase={rootBase}
              providerId={snapshot.provider.id}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

function SettingsSectionPanel({
  snapshot,
  section,
}: {
  snapshot: ProviderSnapshot;
  section: NormalizedSettingsSection | null;
}) {
  const queryClient = useQueryClient();
  const remoteProvider = isRemoteProvider(snapshot.provider) ? snapshot.provider : null;
  const [draft, setDraft] = useState<Record<string, unknown>>(() => buildInitialDraft(section));
  const client = useMemo(
    () => (remoteProvider?.baseUrl ? createApiClient(remoteProvider.baseUrl) : null),
    [remoteProvider?.baseUrl],
  );

  useEffect(() => {
    setDraft(buildInitialDraft(section));
  }, [section]);

  const saveMutation = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      if (!client || !section || section.fields.length === 0) return null;
      const endpoint = section.path ?? `/settings/${section.id}`;
      return client.patch(endpoint, payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['mounted-settings'] });
      await queryClient.invalidateQueries({ queryKey: ['mounted-settings', snapshot.provider.id] });
    },
  });

  if (!section) {
    return (
      <div className="settings-shell__panel settings-shell__panel--empty">
        <p className="settings-shell__panel-title">No settings sections yet</p>
        <p className="settings-shell__panel-copy">
          This provider is mounted, but it did not return any settings sections.
        </p>
      </div>
    );
  }

  const hasWritableFields = Boolean(client && section.fields.some((field) => !field.readOnly));
  const isWritable = Boolean(client && section.writable);

  return (
    <div className="settings-shell__panel">
      <div className="settings-shell__panel-topline">
        <span>
          {snapshot.title.toUpperCase()} · {snapshot.scopeLabel.toUpperCase()}
        </span>
        <span className="settings-shell__panel-code">
          {snapshot.provider.id}.{section.id}
        </span>
      </div>

      <div className="settings-shell__panel-header">
        <div>
          <h1 className="settings-shell__panel-heading">{section.label}</h1>
          <p className="settings-shell__panel-copy">
            {section.description ?? describeScope(snapshot.provider.scope)}
          </p>
        </div>
        <div
          className={cn(
            'settings-shell__panel-badge',
            isWritable
              ? 'settings-shell__panel-badge--editable'
              : 'settings-shell__panel-badge--readonly',
          )}
        >
          {isWritable ? 'Editable' : 'Read only'}
        </div>
      </div>

      {section.fields.length > 0 ? (
        <form
          className="settings-shell__form"
          onSubmit={(event) => {
            event.preventDefault();
            void saveMutation.mutateAsync(draft);
          }}
        >
          <div className="settings-shell__field-list">
            {section.fields.map((field) => (
              <SettingsField
                key={field.key}
                field={field}
                value={draft[field.key]}
                onChange={(nextValue) => {
                  setDraft((current) => ({ ...current, [field.key]: nextValue }));
                }}
              />
            ))}
          </div>

          {hasWritableFields ? (
            <div className="settings-shell__actions">
              <div className="settings-shell__action-controls">
                {saveMutation.isSuccess ? (
                  <span className="settings-shell__status settings-shell__status--success">
                    Saved.
                  </span>
                ) : null}
                {saveMutation.isError ? (
                  <span className="settings-shell__status settings-shell__status--error">
                    Failed to save this section.
                  </span>
                ) : null}
                <button
                  type="submit"
                  disabled={saveMutation.isPending}
                  className="settings-shell__save-button"
                >
                  {saveMutation.isPending ? 'Saving…' : (section.saveLabel ?? 'Save settings')}
                </button>
              </div>
            </div>
          ) : null}
        </form>
      ) : null}

      {section.resources.length > 0 ? (
        <SettingsSectionResources snapshot={snapshot} resources={section.resources} />
      ) : null}
    </div>
  );
}

function ProviderUnavailablePanel({ snapshot }: { snapshot: ProviderSnapshot }) {
  const target =
    isRemoteProvider(snapshot.provider) && snapshot.provider.baseUrl
      ? `${snapshot.provider.baseUrl}/settings`
      : null;

  return (
    <div className="settings-shell__panel settings-shell__panel--empty">
      <div className="settings-shell__panel-topline">
        <span>
          {snapshot.title.toUpperCase()} · {snapshot.scopeLabel.toUpperCase()}
        </span>
        <span className="settings-shell__panel-code">{snapshot.provider.id}.settings</span>
      </div>
      <h1 className="settings-shell__panel-heading">{snapshot.title} Settings</h1>
      <p className="settings-shell__panel-copy">
        {snapshot.status === 'loading'
          ? 'Loading the mounted settings schema…'
          : snapshot.status === 'error'
            ? 'This service responded, but its settings schema could not be loaded.'
            : 'This service does not have a live settings endpoint configured in the current host profile.'}
      </p>
      {target ? (
        <p className="settings-shell__endpoint-note">
          Expected endpoint: <code>{target}</code>
        </p>
      ) : (
        <p className="settings-shell__endpoint-note">
          No service base URL is configured for this provider in the active app profile.
        </p>
      )}
    </div>
  );
}

export function SettingsPage() {
  const providers = useMountedSettingsProviders();
  const { providerId, sectionId } = useParams({ strict: false }) as {
    providerId?: string;
    sectionId?: string;
  };

  const remoteProviders = useMemo(() => providers.filter(isRemoteProvider), [providers]);

  const remoteSchemaQueries = useQueries({
    queries: remoteProviders.map((provider) => ({
      queryKey: ['mounted-settings', provider.id],
      enabled: Boolean(provider.baseUrl),
      queryFn: async () => {
        if (!provider.baseUrl) return null;
        return createApiClient(provider.baseUrl).get<RemoteSettingsProviderSchema>('/settings');
      },
      retry: false,
    })),
  });

  const providerSnapshots = useMemo<ProviderSnapshot[]>(() => {
    const remoteMap = new Map(
      remoteProviders.map((provider, index) => [provider.id, remoteSchemaQueries[index]]),
    );

    return providers.map((provider) => {
      if (!isRemoteProvider(provider)) {
        const sections = provider.sections.map((section) => ({
          id: section.id,
          label: section.label,
          description: section.description,
          fields: [],
          resources: [],
          writable: false,
        }));
        return {
          provider,
          title: provider.title,
          subtitle: provider.subtitle,
          sections,
          status: 'ready' as const,
          scopeLabel: provider.scope,
        };
      }

      const query = remoteMap.get(provider.id);
      const schema = query?.data ?? null;
      const status: ProviderStatus = !provider.baseUrl
        ? 'missing'
        : query?.isLoading
          ? 'loading'
          : query?.isError
            ? 'error'
            : schema
              ? 'ready'
              : 'missing';

      return {
        provider,
        title: schema?.title ?? provider.title,
        subtitle: schema?.subtitle ?? provider.subtitle,
        sections: normalizeSections(schema),
        status,
        scopeLabel: schema?.scope ?? provider.scope,
      };
    });
  }, [providers, remoteProviders, remoteSchemaQueries]);

  const activeSnapshot =
    providerSnapshots.find((snapshot) => snapshot.provider.id === providerId) ??
    providerSnapshots[0] ??
    null;
  const activeSection =
    activeSnapshot?.sections.find((section) => section.id === sectionId) ??
    activeSnapshot?.sections[0] ??
    null;
  const activeProviderId = activeSnapshot?.provider.id;
  const activeSectionId = activeSection?.id;

  return (
    <div className="settings-shell">
      <SettingsSidebar
        snapshots={providerSnapshots}
        activeProviderId={activeProviderId}
        activeSectionId={activeSectionId}
      />

      <main className="settings-shell__main">
        {activeSnapshot ? (
          activeSnapshot.status === 'ready' && activeSection ? (
            <SettingsSectionPanel snapshot={activeSnapshot} section={activeSection} />
          ) : (
            <ProviderUnavailablePanel snapshot={activeSnapshot} />
          )
        ) : (
          <div className="settings-shell__panel settings-shell__panel--empty">
            <h1 className="settings-shell__panel-heading">No settings providers configured</h1>
            <p className="settings-shell__panel-copy">
              Enable at least one service with a mounted settings schema to populate this surface.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
