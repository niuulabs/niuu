import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from '@tanstack/react-router';
import { createApiClient } from '@niuulabs/query';
import { cn } from '@niuulabs/ui';
import {
  useMountedSettingsProviders,
  type MountedSettingsProvider,
  type RemoteSettingsCredentialsResource,
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

type ProviderStatus = 'ready' | 'loading' | 'missing' | 'idle' | 'error';

const CREDENTIAL_ENROLLMENT_STATUS_INTERVAL_MS = 2000;
const TERMINAL_CREDENTIAL_ENROLLMENT_STATES = new Set([
  'complete',
  'failed',
  'expired',
  'cancelled',
]);

interface PersonalAccessTokenRecord {
  id: string;
  name: string;
  createdAt: string;
  lastUsedAt: string | null;
}

interface CreatePersonalAccessTokenResult extends PersonalAccessTokenRecord {
  token: string;
}

interface CredentialSummaryRecord {
  id?: string;
  name: string;
  secretType?: string;
  secret_type?: string;
  keys: string[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
  created_at?: string;
  updatedAt?: string;
  updated_at?: string;
}

interface CredentialListResponse {
  credentials: CredentialSummaryRecord[];
}

interface CredentialTypeFieldDefinition {
  key: string;
  label: string;
  type?: string;
  required?: boolean;
}

interface CredentialTypeDefinition {
  type: string;
  label: string;
  description?: string;
  fields: CredentialTypeFieldDefinition[];
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
  credential_enrollment?: CredentialEnrollmentCatalogSpec | null;
  credentialEnrollment?: CredentialEnrollmentCatalogSpec | null;
}

interface CredentialEnrollmentCatalogSpec {
  method: string;
  credential_field?: string;
  credentialField?: string;
  default_credential_name?: string;
  defaultCredentialName?: string;
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
  config?: Record<string, unknown>;
  credentialStatus?: string;
  credential_status?: string;
  credentialErrorCode?: string | null;
  credential_error_code?: string | null;
  credentialStatusUpdatedAt?: string | null;
  credential_status_updated_at?: string | null;
}

interface CredentialEnrollmentRecord {
  id: string;
  connectionId: string;
  providerSlug: string;
  credentialName: string;
  state: 'pending' | 'awaiting_user' | 'complete' | 'failed' | 'expired' | 'cancelled';
  verificationUri: string;
  userCode: string;
  expiresAt: string;
  errorCode: string;
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
  error?: Error | null;
}

function isApiClientError(
  error: Error | null | undefined,
): error is Error & { status: number; detail?: string } {
  return Boolean(
    error &&
    typeof error === 'object' &&
    'status' in error &&
    typeof (error as { status?: unknown }).status === 'number',
  );
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

function normalizeSections(
  schema: RemoteSettingsProviderSchema | null,
): NormalizedSettingsSection[] {
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
    createdAt:
      (token as { createdAt?: string; created_at?: string }).createdAt ??
      (token as { createdAt?: string; created_at?: string }).created_at ??
      '',
    lastUsedAt:
      (token as { lastUsedAt?: string | null; last_used_at?: string | null }).lastUsedAt ??
      (token as { lastUsedAt?: string | null; last_used_at?: string | null }).last_used_at ??
      null,
    token: (token as { token?: string }).token,
  };
}

function normalizeCredentialRows(payload: CredentialListResponse | CredentialSummaryRecord[]) {
  const rows = Array.isArray(payload) ? payload : payload.credentials;
  return rows.map((credential) => ({
    id: credential.id ?? credential.name,
    name: credential.name,
    secretType: credential.secretType ?? credential.secret_type ?? 'generic',
    keys: credential.keys,
    metadata: credential.metadata ?? {},
    createdAt: credential.createdAt ?? credential.created_at ?? '',
    updatedAt: credential.updatedAt ?? credential.updated_at ?? '',
  }));
}

function normalizeCatalogSchema(
  schema: IntegrationCatalogSchema | undefined,
): IntegrationCatalogSchema {
  return {
    required: schema?.required ?? [],
    properties: schema?.properties ?? {},
  };
}

function isOauthIntegration(entry: IntegrationCatalogEntry | null): boolean {
  if (!entry) return false;
  const authType = entry.authType ?? entry.auth_type;
  return authType === 'oauth2_authorization_code' || authType === 'oauth_token';
}

function isDeviceCodeIntegration(entry: IntegrationCatalogEntry | null): boolean {
  if (!entry) return false;
  return (entry.authType ?? entry.auth_type) === 'device_code';
}

function enrollmentSpec(
  entry: IntegrationCatalogEntry | null,
): CredentialEnrollmentCatalogSpec | null {
  return entry?.credentialEnrollment ?? entry?.credential_enrollment ?? null;
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

function buildInitialResourceValues(
  schema: IntegrationCatalogSchema | undefined,
): Record<string, string> {
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
    credentialStatus: integration.credentialStatus ?? integration.credential_status ?? 'unknown',
    credentialErrorCode:
      integration.credentialErrorCode ?? integration.credential_error_code ?? null,
    credentialStatusUpdatedAt:
      integration.credentialStatusUpdatedAt ?? integration.credential_status_updated_at ?? null,
  };
}

function formatIntegrationType(value: string): string {
  return value.replace(/_/g, ' ');
}

function titleCaseIdentifier(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase());
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
                    : snapshot.status === 'idle'
                      ? 'Select to load'
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
      const rows = await client.get<
        Array<PersonalAccessTokenRecord | CreatePersonalAccessTokenResult>
      >(resource.listPath);
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

function CredentialTypeFields({
  fields,
  values,
  onChange,
}: {
  fields: CredentialTypeFieldDefinition[];
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  if (fields.length === 0) {
    return (
      <div className="settings-resource__schema-fields">
        <label className="settings-resource__composer-field">
          <span className="settings-resource__composer-label">Key</span>
          <input
            value={values.__key ?? ''}
            onChange={(event) => onChange('__key', event.target.value)}
            className="settings-field__control"
          />
        </label>
        <label className="settings-resource__composer-field">
          <span className="settings-resource__composer-label">Value</span>
          <input
            value={values.__value ?? ''}
            onChange={(event) => onChange('__value', event.target.value)}
            className="settings-field__control"
          />
        </label>
      </div>
    );
  }

  return (
    <div className="settings-resource__schema-fields">
      {fields.map((field) => {
        const multiline = field.type === 'textarea';
        return (
          <label
            key={field.key}
            className={cn(
              'settings-resource__composer-field',
              multiline && 'settings-resource__composer-field--wide',
            )}
          >
            <span className="settings-resource__composer-label">
              {field.label}
              {field.required ? (
                <span className="settings-resource__required">required</span>
              ) : null}
            </span>
            {multiline ? (
              <textarea
                value={values[field.key] ?? ''}
                onChange={(event) => onChange(field.key, event.target.value)}
                className="settings-field__textarea"
              />
            ) : (
              <input
                type={field.type === 'password' ? 'password' : 'text'}
                value={values[field.key] ?? ''}
                onChange={(event) => onChange(field.key, event.target.value)}
                className="settings-field__control"
              />
            )}
          </label>
        );
      })}
    </div>
  );
}

function CredentialsResourceCard({
  resource,
  rootBase,
  providerId,
}: {
  resource: RemoteSettingsCredentialsResource;
  rootBase: string;
  providerId: string;
}) {
  const client = useMemo(() => createApiClient(rootBase), [rootBase]);
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [selectedType, setSelectedType] = useState('api_key');
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

  const credentialsQuery = useQuery({
    queryKey: ['settings-resource', providerId, resource.id, 'credentials'],
    queryFn: async () => {
      const payload = await client.get<CredentialListResponse | CredentialSummaryRecord[]>(
        resource.listPath,
      );
      return normalizeCredentialRows(payload);
    },
  });

  const typesQuery = useQuery({
    queryKey: ['settings-resource', providerId, resource.id, 'credential-types'],
    queryFn: () => client.get<CredentialTypeDefinition[]>(resource.typesPath),
  });

  const selectedDefinition = useMemo(() => {
    const types = typesQuery.data ?? [];
    return types.find((entry) => entry.type === selectedType) ?? types[0] ?? null;
  }, [selectedType, typesQuery.data]);

  const createMutation = useMutation({
    mutationFn: async () => {
      const data =
        selectedDefinition?.fields.length === 0
          ? fieldValues.__key?.trim() && fieldValues.__value?.trim()
            ? { [fieldValues.__key.trim()]: fieldValues.__value.trim() }
            : {}
          : Object.fromEntries(
              Object.entries(fieldValues).filter(([, value]) => value.trim() !== ''),
            );
      return client.post(resource.createPath, {
        name,
        secret_type: selectedDefinition?.type ?? selectedType,
        data,
      });
    },
    onSuccess: async () => {
      setName('');
      setFieldValues({});
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'credentials'],
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (credentialName: string) => {
      return client.delete<void>(resource.deletePath.replace('{name}', credentialName));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'credentials'],
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
          if (!name.trim()) return;
          void createMutation.mutateAsync();
        }}
      >
        <div className="settings-resource__schema-fields">
          <label className="settings-resource__composer-field">
            <span className="settings-resource__composer-label">Credential name</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. github-main or prod-openai"
              className="settings-field__control"
            />
          </label>

          <label className="settings-resource__composer-field">
            <span className="settings-resource__composer-label">Credential type</span>
            <div className="settings-field__control-wrap">
              <select
                value={selectedDefinition?.type ?? selectedType}
                onChange={(event) => setSelectedType(event.target.value)}
                className="settings-field__control"
              >
                {(typesQuery.data ?? []).map((type) => (
                  <option key={type.type} value={type.type}>
                    {type.label}
                  </option>
                ))}
              </select>
              <span className="settings-field__select-caret" aria-hidden="true">
                ▾
              </span>
            </div>
          </label>
        </div>

        {selectedDefinition?.description ? (
          <p className="settings-resource__copy">{selectedDefinition.description}</p>
        ) : null}

        {selectedDefinition ? (
          <CredentialTypeFields
            fields={selectedDefinition.fields}
            values={fieldValues}
            onChange={(key, value) => {
              setFieldValues((current) => ({ ...current, [key]: value }));
            }}
          />
        ) : null}

        <div className="settings-resource__actions">
          {createMutation.isError ? (
            <span className="settings-shell__status settings-shell__status--error">
              Could not store this credential.
            </span>
          ) : null}
          {createMutation.isSuccess ? (
            <span className="settings-shell__status settings-shell__status--success">
              Credential stored.
            </span>
          ) : null}
          <button
            type="submit"
            className="settings-shell__save-button settings-shell__save-button--secondary"
            disabled={
              createMutation.isPending ||
              !name.trim() ||
              !selectedDefinition ||
              (selectedDefinition.fields.length === 0 &&
                (!fieldValues.__key?.trim() || !fieldValues.__value?.trim()))
            }
          >
            {createMutation.isPending ? 'Storing…' : 'Store credential'}
          </button>
        </div>
      </form>

      <div className="settings-resource__list">
        {credentialsQuery.isLoading ? (
          <div className="settings-resource__empty">Loading credentials…</div>
        ) : credentialsQuery.isError ? (
          <div className="settings-resource__empty">Could not load credentials.</div>
        ) : credentialsQuery.data && credentialsQuery.data.length > 0 ? (
          credentialsQuery.data.map((credential) => (
            <div key={credential.id} className="settings-resource__row">
              <div className="settings-resource__row-main">
                <div className="settings-resource__row-title">{credential.name}</div>
                <div className="settings-resource__row-meta">
                  {titleCaseIdentifier(credential.secretType)} · keys{' '}
                  {credential.keys.join(', ') || '—'}
                </div>
              </div>
              <button
                type="button"
                className="settings-resource__row-action"
                disabled={deleteMutation.isPending}
                onClick={() => {
                  void deleteMutation.mutateAsync(credential.name);
                }}
              >
                Delete
              </button>
            </div>
          ))
        ) : (
          <div className="settings-resource__empty">No credentials stored yet.</div>
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
  const client = createApiClient(rootBase);
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState('');
  const [credentialName, setCredentialName] = useState('');
  const [selectedExistingCredential, setSelectedExistingCredential] = useState('');
  const [createInlineCredential, setCreateInlineCredential] = useState(true);
  const [credentialValues, setCredentialValues] = useState<Record<string, string>>({});
  const [configValues, setConfigValues] = useState<Record<string, string>>({});
  const [oauthPendingSlug, setOauthPendingSlug] = useState<string | null>(null);
  const [credentialEnrollment, setCredentialEnrollment] =
    useState<CredentialEnrollmentRecord | null>(null);
  const [lastTestStatus, setLastTestStatus] = useState<Record<string, string>>({});

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

  const credentialsQuery = useQuery({
    queryKey: ['settings-resource', providerId, resource.id, 'credentials'],
    queryFn: async () => {
      const payload = await client.get<CredentialListResponse | CredentialSummaryRecord[]>(
        resource.credentialListPath,
      );
      return normalizeCredentialRows(payload);
    },
  });

  const catalogEntries = catalogQuery.data ?? [];
  const selectedCatalogId = selectedId || (catalogEntries[0]?.slug ?? catalogEntries[0]?.id ?? '');
  const selectedEntry =
    catalogEntries.find((entry) => (entry.slug ?? entry.id) === selectedCatalogId) ?? null;

  const selectedConnection =
    integrationsQuery.data?.find((integration) => (integration.slug ?? '') === selectedCatalogId) ??
    null;
  const selectedEnrollmentSpec = enrollmentSpec(selectedEntry);

  const credentialSchema = useMemo(
    () =>
      normalizeCatalogSchema(selectedEntry?.credentialSchema ?? selectedEntry?.credential_schema),
    [selectedEntry],
  );
  const configSchema = useMemo(
    () => normalizeCatalogSchema(selectedEntry?.configSchema ?? selectedEntry?.config_schema),
    [selectedEntry],
  );
  const effectiveCredentialName =
    credentialName ||
    selectedEnrollmentSpec?.defaultCredentialName ||
    selectedEnrollmentSpec?.default_credential_name ||
    (selectedEntry ? `${selectedCatalogId}-credential` : '');
  const effectiveCredentialValues =
    Object.keys(credentialValues).length > 0
      ? credentialValues
      : buildInitialResourceValues(credentialSchema);
  const effectiveConfigValues =
    Object.keys(configValues).length > 0 ? configValues : buildInitialResourceValues(configSchema);

  useEffect(() => {
    if (!oauthPendingSlug) return;
    if (typeof window === 'undefined') return;

    const intervalId = window.setInterval(() => {
      void integrationsQuery.refetch().then((result) => {
        const found = result.data?.find((integration) => integration.slug === oauthPendingSlug);
        if (found) {
          setOauthPendingSlug(null);
        }
      });
    }, 2000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [integrationsQuery, oauthPendingSlug]);

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedEntry) {
        throw new Error('No integration selected');
      }

      const configPayload = Object.fromEntries(
        Object.entries(effectiveConfigValues)
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
        slug: selectedEntry.slug ?? selectedEntry.id,
        config: configPayload,
        enabled: true,
        ...(createInlineCredential
          ? {
              credential: {
                name: effectiveCredentialName,
                data: Object.fromEntries(
                  Object.entries(effectiveCredentialValues).filter(
                    ([, value]) => value.trim() !== '',
                  ),
                ),
              },
            }
          : {
              credential_name: selectedExistingCredential,
            }),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'integrations'],
      });
      setOauthPendingSlug(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (integration: { id: string; slug?: string; oauth: boolean }) => {
      if (integration.oauth && integration.slug) {
        return client.post<void>(
          resource.oauthDisconnectPath.replace('{slug}', integration.slug),
          {},
        );
      }
      return client.delete<void>(resource.deletePath.replace('{id}', integration.id));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['settings-resource', providerId, resource.id, 'integrations'],
      });
    },
  });

  const testMutation = useMutation({
    mutationFn: async (integrationId: string) => {
      return client.post<{
        success: boolean;
        provider: string;
        workspace?: string | null;
        user?: string | null;
        error?: string | null;
      }>(resource.testPath.replace('{id}', integrationId), {});
    },
    onSuccess: (result, integrationId) => {
      setLastTestStatus((current) => ({
        ...current,
        [integrationId]: result.success
          ? `Connected to ${result.provider}${result.workspace ? ` · ${result.workspace}` : ''}`
          : result.error || 'Connection test failed',
      }));
    },
    onError: (_error, integrationId) => {
      setLastTestStatus((current) => ({
        ...current,
        [integrationId]: 'Connection test failed',
      }));
    },
  });

  const oauthMutation = useMutation({
    mutationFn: async (slug: string) => {
      return client.get<{ url: string }>(resource.oauthAuthorizePath.replace('{slug}', slug));
    },
    onSuccess: (payload, slug) => {
      setOauthPendingSlug(slug);
      if (typeof window !== 'undefined') {
        window.open(payload.url, `${slug}-oauth`, 'popup,width=720,height=840');
      }
    },
  });

  const enrollmentMutation = useMutation({
    mutationFn: async () => {
      if (!selectedEntry || !resource.enrollmentStartPath) {
        throw new Error('Interactive credential enrollment is unavailable');
      }
      return client.post<CredentialEnrollmentRecord>(resource.enrollmentStartPath, {
        slug: selectedEntry.slug ?? selectedEntry.id,
        credential_name: effectiveCredentialName,
        connection_id: selectedConnection?.id ?? '',
      });
    },
    onSuccess: (enrollment) => {
      setCredentialEnrollment(enrollment);
    },
  });

  const enrollmentQuery = useQuery({
    queryKey: [
      'settings-resource',
      providerId,
      resource.id,
      'credential-enrollment',
      credentialEnrollment?.id,
    ],
    enabled: Boolean(
      credentialEnrollment?.id &&
      resource.enrollmentStatusPath &&
      !TERMINAL_CREDENTIAL_ENROLLMENT_STATES.has(credentialEnrollment.state),
    ),
    queryFn: () =>
      client.get<CredentialEnrollmentRecord>(
        resource.enrollmentStatusPath!.replace('{id}', credentialEnrollment!.id),
      ),
    refetchInterval: (query) => {
      const state = (query.state.data as CredentialEnrollmentRecord | undefined)?.state;
      return state && TERMINAL_CREDENTIAL_ENROLLMENT_STATES.has(state)
        ? false
        : CREDENTIAL_ENROLLMENT_STATUS_INTERVAL_MS;
    },
  });

  const currentEnrollment = enrollmentQuery.data ?? credentialEnrollment;

  useEffect(() => {
    if (currentEnrollment?.state !== 'complete') return;
    void queryClient.invalidateQueries({
      queryKey: ['settings-resource', providerId, resource.id, 'integrations'],
    });
  }, [currentEnrollment?.state, providerId, queryClient, resource.id]);

  const cancelEnrollmentMutation = useMutation({
    mutationFn: async (enrollmentId: string) => {
      if (!resource.enrollmentCancelPath) {
        throw new Error('Interactive credential enrollment is unavailable');
      }
      return client.delete<CredentialEnrollmentRecord>(
        resource.enrollmentCancelPath.replace('{id}', enrollmentId),
      );
    },
    onSuccess: (enrollment) => {
      setCredentialEnrollment(enrollment);
    },
  });

  const selectedIsOauth = isOauthIntegration(selectedEntry);
  const selectedIsDeviceCode = isDeviceCodeIntegration(selectedEntry);
  const selectedCredentialReady = ['active', 'configured'].includes(
    selectedConnection?.credentialStatus ?? '',
  );
  const availableCredentials = credentialsQuery.data ?? [];

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

      <div className="settings-resource__list settings-resource__list--catalog">
        {(catalogQuery.data ?? []).map((entry) => {
          const key = entry.slug ?? entry.id;
          const connected = integrationsQuery.data?.some((integration) => integration.slug === key);
          return (
            <button
              key={key}
              type="button"
              className={cn(
                'settings-resource__catalog-card',
                key === selectedCatalogId && 'settings-resource__catalog-card--active',
              )}
              onClick={() => {
                setSelectedId(key);
                const spec = enrollmentSpec(entry);
                setCredentialName(
                  spec?.defaultCredentialName ??
                    spec?.default_credential_name ??
                    `${key}-credential`,
                );
                setCredentialEnrollment(null);
                setSelectedExistingCredential('');
                setCredentialValues(
                  buildInitialResourceValues(
                    normalizeCatalogSchema(entry.credentialSchema ?? entry.credential_schema),
                  ),
                );
                setConfigValues(
                  buildInitialResourceValues(
                    normalizeCatalogSchema(entry.configSchema ?? entry.config_schema),
                  ),
                );
              }}
            >
              <span className="settings-resource__catalog-title">{entry.name}</span>
              <span className="settings-resource__catalog-meta">
                {formatIntegrationType(
                  entry.integrationType ?? entry.integration_type ?? 'integration',
                )}
                {connected ? ' · connected' : ''}
              </span>
            </button>
          );
        })}
      </div>

      {selectedEntry ? (
        <form
          className="settings-resource__composer settings-resource__composer--stacked"
          onSubmit={(event) => {
            event.preventDefault();
            if (!selectedEntry) return;
            if (selectedIsDeviceCode || selectedIsOauth) return;
            if (!selectedIsOauth && createInlineCredential && !credentialName.trim()) return;
            if (!selectedIsOauth && !createInlineCredential && !selectedExistingCredential) return;
            void createMutation.mutateAsync();
          }}
        >
          <div className="settings-resource__header">
            <div>
              <h3 className="settings-resource__title settings-resource__title--compact">
                {selectedEntry.name}
              </h3>
              {selectedEntry.description ? (
                <p className="settings-resource__copy">{selectedEntry.description}</p>
              ) : null}
            </div>
          </div>

          {selectedIsDeviceCode ? (
            <div className="settings-resource__actions">
              {selectedConnection ? (
                <span
                  className={cn(
                    'settings-shell__status',
                    selectedCredentialReady
                      ? 'settings-shell__status--success'
                      : 'settings-shell__status--error',
                  )}
                >
                  {selectedCredentialReady
                    ? `Connected as ${selectedConnection.credentialName}`
                    : 'Reconnect required'}
                </span>
              ) : null}
              {currentEnrollment?.state === 'awaiting_user' ? (
                <div className="settings-resource__row-note">
                  Open{' '}
                  <a href={currentEnrollment.verificationUri} target="_blank" rel="noreferrer">
                    the provider login page
                  </a>{' '}
                  and enter code <strong>{currentEnrollment.userCode}</strong>.
                </div>
              ) : null}
              {currentEnrollment?.state === 'complete' ? (
                <span className="settings-shell__status settings-shell__status--success">
                  Codex account connected.
                </span>
              ) : null}
              {currentEnrollment &&
              TERMINAL_CREDENTIAL_ENROLLMENT_STATES.has(currentEnrollment.state) &&
              currentEnrollment.state !== 'complete' ? (
                <span className="settings-shell__status settings-shell__status--error">
                  Login {currentEnrollment.state.replace('_', ' ')}. Start it again to retry.
                </span>
              ) : null}
              {enrollmentMutation.isError ? (
                <span className="settings-shell__status settings-shell__status--error">
                  Could not start the Codex login. Retry or contact an administrator.
                </span>
              ) : null}
              {enrollmentQuery.isError ? (
                <span className="settings-shell__status settings-shell__status--error">
                  Could not read the Codex login status. The login remains safe to retry.
                </span>
              ) : null}
              {cancelEnrollmentMutation.isError ? (
                <span className="settings-shell__status settings-shell__status--error">
                  Could not cancel the Codex login. It will be removed automatically after expiry.
                </span>
              ) : null}
              <button
                type="button"
                className="settings-shell__save-button settings-shell__save-button--secondary"
                disabled={
                  enrollmentMutation.isPending ||
                  currentEnrollment?.state === 'pending' ||
                  currentEnrollment?.state === 'awaiting_user' ||
                  !resource.enrollmentStartPath
                }
                onClick={() => {
                  enrollmentMutation.mutate();
                }}
              >
                {enrollmentMutation.isPending
                  ? 'Starting…'
                  : selectedConnection
                    ? 'Reconnect Codex'
                    : 'Connect Codex'}
              </button>
              {currentEnrollment?.state === 'awaiting_user' ? (
                <button
                  type="button"
                  className="settings-resource__row-action"
                  disabled={cancelEnrollmentMutation.isPending}
                  onClick={() => {
                    cancelEnrollmentMutation.mutate(currentEnrollment.id);
                  }}
                >
                  Cancel login
                </button>
              ) : null}
            </div>
          ) : selectedIsOauth ? (
            <div className="settings-resource__actions">
              {selectedConnection ? (
                <span className="settings-shell__status settings-shell__status--success">
                  Connected as {selectedConnection.credentialName}
                </span>
              ) : null}
              {oauthPendingSlug === (selectedEntry.slug ?? selectedEntry.id) ? (
                <span className="settings-shell__status">Waiting for OAuth confirmation…</span>
              ) : null}
              <button
                type="button"
                className="settings-shell__save-button settings-shell__save-button--secondary"
                disabled={oauthMutation.isPending || !!selectedConnection}
                onClick={() => {
                  void oauthMutation.mutateAsync(selectedEntry.slug ?? selectedEntry.id);
                }}
              >
                {oauthMutation.isPending ? 'Opening…' : 'Connect with OAuth'}
              </button>
            </div>
          ) : (
            <>
              <label className="settings-field settings-field--toggle settings-resource__toggle">
                <div className="settings-field__meta">
                  <span className="settings-field__label">Create a new credential</span>
                  <span className="settings-field__description">
                    Turn this off to attach an existing stored credential instead.
                  </span>
                </div>
                <span className="settings-checkbox">
                  <input
                    type="checkbox"
                    checked={createInlineCredential}
                    onChange={(event) => setCreateInlineCredential(event.target.checked)}
                    className="settings-checkbox__input"
                  />
                  <span className="settings-checkbox__ui">
                    <span
                      className={cn(
                        'settings-checkbox__box',
                        createInlineCredential && 'settings-checkbox__box--checked',
                      )}
                      aria-hidden="true"
                    >
                      {createInlineCredential ? '✓' : ''}
                    </span>
                    <span className="settings-checkbox__label" aria-hidden="true">
                      {createInlineCredential ? 'Enabled' : 'Disabled'}
                    </span>
                  </span>
                </span>
              </label>

              {createInlineCredential ? (
                <>
                  <label className="settings-resource__composer-field">
                    <span className="settings-resource__composer-label">Credential name</span>
                    <input
                      value={effectiveCredentialName}
                      onChange={(event) => setCredentialName(event.target.value)}
                      className="settings-field__control"
                    />
                  </label>
                  <IntegrationSchemaFields
                    label="Credential"
                    schema={credentialSchema}
                    values={effectiveCredentialValues}
                    onChange={(key, value) => {
                      setCredentialValues((current) => ({ ...current, [key]: value }));
                    }}
                  />
                </>
              ) : (
                <label className="settings-resource__composer-field">
                  <span className="settings-resource__composer-label">Stored credential</span>
                  <div className="settings-field__control-wrap">
                    <select
                      value={selectedExistingCredential}
                      onChange={(event) => setSelectedExistingCredential(event.target.value)}
                      className="settings-field__control"
                    >
                      <option value="">Choose a stored credential</option>
                      {availableCredentials.map((credential) => (
                        <option key={credential.id} value={credential.name}>
                          {credential.name}
                        </option>
                      ))}
                    </select>
                    <span className="settings-field__select-caret" aria-hidden="true">
                      ▾
                    </span>
                  </div>
                </label>
              )}

              <IntegrationSchemaFields
                label="Connection config"
                schema={configSchema}
                values={effectiveConfigValues}
                onChange={(key, value) => {
                  setConfigValues((current) => ({ ...current, [key]: value }));
                }}
              />

              <div className="settings-resource__actions">
                {createMutation.isError ? (
                  <span className="settings-shell__status settings-shell__status--error">
                    Could not connect this integration.
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
                  disabled={
                    createMutation.isPending ||
                    (createInlineCredential
                      ? !effectiveCredentialName.trim()
                      : !selectedExistingCredential)
                  }
                >
                  {createMutation.isPending ? 'Connecting…' : 'Connect integration'}
                </button>
              </div>
            </>
          )}
        </form>
      ) : null}

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
                  {formatIntegrationType(integration.integrationType)} ·{' '}
                  {integration.credentialName} · {integration.enabled ? 'enabled' : 'disabled'} ·{' '}
                  {integration.credentialStatus.replace(/_/g, ' ')}
                </div>
                {lastTestStatus[integration.id] ? (
                  <div className="settings-resource__row-note">
                    {lastTestStatus[integration.id]}
                  </div>
                ) : null}
              </div>
              <div className="settings-resource__row-actions">
                <button
                  type="button"
                  className="settings-resource__row-action"
                  disabled={testMutation.isPending}
                  onClick={() => {
                    void testMutation.mutateAsync(integration.id);
                  }}
                >
                  Test
                </button>
                <button
                  type="button"
                  className="settings-resource__row-action"
                  disabled={deleteMutation.isPending}
                  onClick={() => {
                    void deleteMutation.mutateAsync({
                      id: integration.id,
                      slug: integration.slug,
                      oauth: isOauthIntegration(
                        (catalogQuery.data ?? []).find(
                          (entry) => (entry.slug ?? entry.id) === integration.slug,
                        ) ?? null,
                      ),
                    });
                  }}
                >
                  Disconnect
                </button>
              </div>
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
  const rootBase = remoteProvider?.baseUrl ? resolveRootBase(remoteProvider.baseUrl) : null;

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
        if (resource.type === 'credentials') {
          return (
            <CredentialsResourceCard
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
  const client = remoteProvider?.baseUrl ? createApiClient(remoteProvider.baseUrl) : null;

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
  const apiError = isApiClientError(snapshot.error) ? snapshot.error : null;
  let copy =
    'This service does not have a live settings endpoint configured in the current host profile.';

  if (snapshot.status === 'loading') {
    copy = 'Loading the mounted settings schema…';
  } else if (snapshot.status === 'idle') {
    copy = 'Select this provider to load its mounted settings schema.';
  } else if (snapshot.status === 'error') {
    if (apiError?.status === 401) {
      copy = 'This service rejected the current token while loading its settings schema.';
    } else if (apiError?.status === 403) {
      copy = 'You do not have permission to view this service settings surface.';
    } else if (apiError?.status === 404) {
      copy = 'This service is mounted, but it does not expose the expected settings route.';
    } else if (apiError?.status === 503) {
      copy = 'This service is configured, but it is not currently available.';
    } else {
      copy = 'This service responded, but its settings schema could not be loaded.';
    }
  }

  return (
    <div className="settings-shell__panel settings-shell__panel--empty">
      <div className="settings-shell__panel-topline">
        <span>
          {snapshot.title.toUpperCase()} · {snapshot.scopeLabel.toUpperCase()}
        </span>
        <span className="settings-shell__panel-code">{snapshot.provider.id}.settings</span>
      </div>
      <h1 className="settings-shell__panel-heading">{snapshot.title} Settings</h1>
      <p className="settings-shell__panel-copy">{copy}</p>
      {apiError?.detail ? (
        <p className="settings-shell__endpoint-note">
          Response detail: <code>{apiError.detail}</code>
        </p>
      ) : null}
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
  const activeProvider =
    providers.find((provider) => provider.id === providerId) ?? providers[0] ?? null;
  const activeRemoteProviderId =
    activeProvider && isRemoteProvider(activeProvider) ? activeProvider.id : null;

  const remoteProviders = useMemo(() => providers.filter(isRemoteProvider), [providers]);

  const remoteSchemaQueries = useQueries({
    queries: remoteProviders.map((provider) => ({
      queryKey: ['mounted-settings', provider.id],
      enabled: Boolean(provider.baseUrl) && provider.id === activeRemoteProviderId,
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
        : provider.id !== activeRemoteProviderId
          ? 'idle'
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
        error: (query?.error as Error | undefined) ?? null,
      };
    });
  }, [activeRemoteProviderId, providers, remoteProviders, remoteSchemaQueries]);

  const activeSnapshot =
    providerSnapshots.find((snapshot) => snapshot.provider.id === activeProvider?.id) ??
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
            <SettingsSectionPanel
              key={activeSection ? `${activeSnapshot.provider.id}:${activeSection.id}` : 'empty'}
              snapshot={activeSnapshot}
              section={activeSection}
            />
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
