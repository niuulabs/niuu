import { useEffect, useMemo, useState } from 'react';
import { Link, useParams, useRouter } from '@tanstack/react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createApiClient } from '@niuulabs/query';
import { cn } from '@niuulabs/ui';
import {
  useMountedSettingsProviders,
  type MountedSettingsProvider,
  type RemoteSettingsProviderSchema,
  type RemoteSettingsSectionSchema,
} from './SettingsRegistry';

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

function SettingsBreadcrumb({
  provider,
  sectionLabel,
}: {
  provider: MountedSettingsProvider | null;
  sectionLabel?: string;
}) {
  const router = useRouter();

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-3">
      <button
        type="button"
        onClick={() => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          void router.navigate({ to: '/settings' as any });
        }}
        className="niuu-text-sm niuu-text-text-secondary hover:niuu-text-text-primary niuu-transition-colors"
        aria-label="Back to settings"
      >
        ← Settings
      </button>
      {provider ? (
        <>
          <span className="niuu-text-text-muted">/</span>
          <span className="niuu-text-sm niuu-text-text-primary niuu-font-medium">
            {sectionLabel ?? provider.title}
          </span>
        </>
      ) : null}
    </div>
  );
}

function ProviderTabs({
  providers,
  activeProviderId,
}: {
  providers: MountedSettingsProvider[];
  activeProviderId?: string;
}) {
  const router = useRouter();

  return (
    <nav
      className="niuu-flex niuu-items-center niuu-gap-2 niuu-overflow-x-auto niuu-border-b niuu-border-border niuu-pb-3"
      aria-label="Settings providers"
    >
      {providers.map((provider) => {
        const isActive = provider.id === activeProviderId;
        return (
          <button
            key={provider.id}
            type="button"
            onClick={() => {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              void router.navigate({ to: providerPath(provider.id) as any });
            }}
            className={cn(
              'niuu-whitespace-nowrap niuu-rounded-full niuu-border niuu-px-4 niuu-py-2 niuu-text-sm niuu-font-medium niuu-transition-colors',
              isActive
                ? 'niuu-border-border-strong niuu-bg-bg-elevated niuu-text-text-primary'
                : 'niuu-border-border niuu-text-text-secondary hover:niuu-border-border-strong hover:niuu-text-text-primary hover:niuu-bg-bg-secondary',
            )}
            aria-current={isActive ? 'page' : undefined}
          >
            {provider.title}
          </button>
        );
      })}
    </nav>
  );
}

function SettingsSectionRail({
  provider,
  activeSectionId,
  remoteSections,
}: {
  provider: MountedSettingsProvider;
  activeSectionId?: string;
  remoteSections?: Array<Pick<RemoteSettingsSectionSchema, 'id' | 'label'>>;
}) {
  const router = useRouter();
  const sections =
    provider.source === 'local'
      ? provider.sections
      : (remoteSections ?? []).map((section) => ({
          ...section,
          description: '',
        }));

  if (sections.length === 0) return null;

  return (
    <aside className="niuu-flex niuu-flex-col niuu-gap-0.5 niuu-w-full lg:niuu-w-[240px] lg:niuu-shrink-0 niuu-self-stretch niuu-border-r niuu-border-[#26272d] niuu-bg-[#1a1b1f] niuu-px-3 niuu-py-4">
      <p className="niuu-px-2 niuu-py-1 niuu-text-xs niuu-uppercase niuu-tracking-[0.18em] niuu-text-[#767982]">
        Settings
      </p>
      {sections.map((section) => {
        const isActive = section.id === activeSectionId;
        return (
          <button
            key={section.id}
            type="button"
            onClick={() => {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              void router.navigate({ to: providerPath(provider.id, section.id) as any });
            }}
            className={cn(
              'niuu-w-full niuu-text-left niuu-rounded-sm niuu-px-3 niuu-py-1.5 niuu-text-sm niuu-transition-colors',
              isActive
                ? 'niuu-bg-[#22242a] niuu-text-[#f2f3f5] niuu-font-medium'
                : 'niuu-text-[#a3a6ae] hover:niuu-bg-[#1f2126] hover:niuu-text-[#f2f3f5]',
            )}
            aria-current={isActive ? 'page' : undefined}
          >
            {section.label}
          </button>
        );
      })}
    </aside>
  );
}

function ProvidersOverview({ providers }: { providers: MountedSettingsProvider[] }) {
  return (
    <div className="niuu-max-w-[720px]">
      <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary niuu-mb-2">Settings</h2>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-6">
        Choose a mounted settings surface to configure.
      </p>
      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-list-none niuu-p-0 niuu-m-0">
        {providers.map((provider) => {
          return (
            <Link
              key={provider.id}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              to={providerPath(provider.id) as any}
              className="niuu-block niuu-w-full niuu-text-left niuu-p-4 niuu-border niuu-border-border niuu-rounded-md hover:niuu-bg-bg-secondary niuu-transition-colors niuu-no-underline"
            >
              <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-1">
                {provider.title}
              </p>
              <p className="niuu-text-xs niuu-text-text-secondary">{provider.subtitle}</p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function ProviderOverview({
  provider,
  remoteSections = [],
}: {
  provider: MountedSettingsProvider;
  remoteSections?: RemoteSettingsSectionSchema[];
}) {
  if (provider.source === 'local') {
    return (
      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-list-none niuu-p-0 niuu-m-0">
        {provider.sections.map((section) => (
          <Link
            key={section.id}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            to={providerPath(provider.id, section.id) as any}
            className="niuu-block niuu-w-full niuu-text-left niuu-p-4 niuu-border niuu-border-border niuu-rounded-md hover:niuu-bg-bg-secondary niuu-transition-colors niuu-no-underline"
          >
            <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-1">
              {section.label}
            </p>
            <p className="niuu-text-xs niuu-text-text-secondary">{section.description}</p>
          </Link>
        ))}
      </div>
    );
  }

  if (remoteSections.length > 0) {
    return (
      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3 niuu-list-none niuu-p-0 niuu-m-0">
        {remoteSections.map((section) => (
          <Link
            key={section.id}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            to={providerPath(provider.id, section.id) as any}
            className="niuu-block niuu-w-full niuu-text-left niuu-p-4 niuu-border niuu-border-border niuu-rounded-md hover:niuu-bg-bg-secondary niuu-transition-colors niuu-no-underline"
          >
            <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-1">
              {section.label}
            </p>
            <p className="niuu-text-xs niuu-text-text-secondary">
              {section.description ?? 'Mounted from the provider settings schema.'}
            </p>
          </Link>
        ))}
      </div>
    );
  }

  return (
    <div className="niuu-rounded-md niuu-border niuu-border-dashed niuu-border-border niuu-p-6">
      <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-2">
        {provider.title} settings endpoint
      </p>
      <p className="niuu-text-sm niuu-text-text-secondary">
        This provider will mount dynamically from{' '}
        <code>{provider.baseUrl ? `${provider.baseUrl}/settings` : '(not configured)'}</code> when
        its settings endpoint is available.
      </p>
    </div>
  );
}

function RemoteSettingsSection({
  provider,
  schema,
  sectionId,
}: {
  provider: Extract<MountedSettingsProvider, { source: 'remote' }>;
  schema: RemoteSettingsProviderSchema;
  sectionId?: string;
}) {
  const queryClient = useQueryClient();
  const section = useMemo(
    () => schema.sections.find((entry) => entry.id === sectionId) ?? schema.sections[0] ?? null,
    [schema.sections, sectionId],
  );
  const [draft, setDraft] = useState<Record<string, unknown>>(() => buildInitialDraft(section));
  const client = useMemo(
    () => (provider.baseUrl ? createApiClient(provider.baseUrl) : null),
    [provider.baseUrl],
  );
  const isWritable = Boolean(client && section?.fields.some((field) => !field.readOnly));

  useEffect(() => {
    setDraft(buildInitialDraft(section));
  }, [section]);

  const saveMutation = useMutation({
    mutationFn: async (payload: Record<string, unknown>) => {
      if (!client || !section) return null;
      const endpoint = section.path ?? `/settings/${section.id}`;
      return client.patch(endpoint, payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['mounted-settings', provider.id] });
    },
  });

  if (!section) {
    return (
      <div className="niuu-rounded-md niuu-border niuu-border-border niuu-p-6">
        <p className="niuu-text-sm niuu-text-text-secondary">
          No sections were returned by this settings endpoint.
        </p>
      </div>
    );
  }

  return (
    <div className="niuu-space-y-4">
      <div>
        <h2 className="niuu-text-xl niuu-font-semibold niuu-text-text-primary">{section.label}</h2>
        {section.description ? (
          <p className="niuu-text-sm niuu-text-text-secondary niuu-mt-1">{section.description}</p>
        ) : null}
      </div>

      <form
        className="niuu-space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void saveMutation.mutateAsync(draft);
        }}
      >
        {section.fields.map((field) => {
          const value = draft[field.key];
          return (
            <label key={field.key} className="niuu-block">
              <span className="niuu-block niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-1">
                {field.label}
              </span>
              {field.description ? (
                <span className="niuu-block niuu-text-xs niuu-text-text-muted niuu-mb-2">
                  {field.description}
                </span>
              ) : null}
              {field.type === 'textarea' ? (
                <textarea
                  value={String(value ?? '')}
                  placeholder={field.placeholder}
                  readOnly={field.readOnly}
                  onChange={(event) => {
                    setDraft((current) => ({ ...current, [field.key]: event.target.value }));
                  }}
                  className="niuu-w-full niuu-min-h-[120px] niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-text-sm"
                />
              ) : field.type === 'boolean' ? (
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  disabled={field.readOnly}
                  onChange={(event) => {
                    setDraft((current) => ({ ...current, [field.key]: event.target.checked }));
                  }}
                  className="niuu-h-4 niuu-w-4"
                />
              ) : field.type === 'select' ? (
                <select
                  value={String(value ?? '')}
                  disabled={field.readOnly}
                  onChange={(event) => {
                    setDraft((current) => ({ ...current, [field.key]: event.target.value }));
                  }}
                  className="niuu-w-full niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-text-sm"
                >
                  {(field.options ?? []).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type={field.secret ? 'password' : field.type === 'number' ? 'number' : 'text'}
                  value={String(value ?? '')}
                  placeholder={field.placeholder}
                  readOnly={field.readOnly}
                  onChange={(event) => {
                    const nextValue =
                      field.type === 'number'
                        ? Number(event.target.value || 0)
                        : event.target.value;
                    setDraft((current) => ({ ...current, [field.key]: nextValue }));
                  }}
                  className="niuu-w-full niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-text-sm"
                />
              )}
            </label>
          );
        })}

        <div className="niuu-flex niuu-items-center niuu-gap-3">
          <button
            type="submit"
            disabled={saveMutation.isPending || !isWritable}
            className="niuu-rounded-md niuu-bg-bg-elevated niuu-px-4 niuu-py-2 niuu-text-sm niuu-font-medium niuu-text-text-primary disabled:niuu-opacity-50"
          >
            {saveMutation.isPending
              ? 'Saving…'
              : isWritable
                ? (section.saveLabel ?? 'Save settings')
                : 'Read only'}
          </button>
          {saveMutation.isSuccess ? (
            <span className="niuu-text-xs niuu-text-text-secondary">Saved.</span>
          ) : null}
          {saveMutation.isError ? (
            <span className="niuu-text-xs niuu-text-danger">Failed to save this section.</span>
          ) : null}
        </div>
      </form>
    </div>
  );
}

export function SettingsPage() {
  const providers = useMountedSettingsProviders();
  const { providerId, sectionId } = useParams({ strict: false }) as {
    providerId?: string;
    sectionId?: string;
  };
  const selectedProvider = providers.find((provider) => provider.id === providerId) ?? null;
  const remoteSchemaQuery = useQuery({
    queryKey: ['mounted-settings', selectedProvider?.id],
    enabled: Boolean(
      selectedProvider && isRemoteProvider(selectedProvider) && selectedProvider.baseUrl,
    ),
    queryFn: async () => {
      if (!selectedProvider || !isRemoteProvider(selectedProvider) || !selectedProvider.baseUrl) {
        return null;
      }
      return createApiClient(selectedProvider.baseUrl).get<RemoteSettingsProviderSchema>(
        '/settings',
      );
    },
  });

  const remoteSchema = remoteSchemaQuery.data;
  const remoteSections = remoteSchema?.sections ?? [];
  const effectiveSectionId = sectionId;
  const remoteProviderForRender =
    selectedProvider && isRemoteProvider(selectedProvider) ? selectedProvider : null;
  const activeSectionLabel =
    selectedProvider?.source === 'local'
      ? selectedProvider.sections.find((section) => section.id === effectiveSectionId)?.label
      : remoteSections.find((section) => section.id === effectiveSectionId)?.label;

  return (
    <div className="niuu-p-6 niuu-space-y-6">
      <ProviderTabs providers={providers} activeProviderId={selectedProvider?.id} />

      {!selectedProvider ? (
        <ProvidersOverview providers={providers} />
      ) : (
        <div className="niuu-flex niuu-flex-col lg:niuu-flex-row lg:niuu-items-start niuu-gap-6">
          <SettingsSectionRail
            provider={selectedProvider}
            activeSectionId={effectiveSectionId}
            remoteSections={selectedProvider.source === 'remote' ? remoteSections : undefined}
          />

          <main className="niuu-flex-1 niuu-space-y-6">
            <SettingsBreadcrumb provider={selectedProvider} sectionLabel={activeSectionLabel} />

            <div className="niuu-max-w-[720px]">
              <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary niuu-mb-2">
                {selectedProvider.title} Settings
              </h2>
              {selectedProvider.subtitle ? (
                <p className="niuu-text-sm niuu-text-text-secondary">{selectedProvider.subtitle}</p>
              ) : null}
            </div>

            {selectedProvider.source === 'local' ? (
              effectiveSectionId ? (
                <div className="niuu-max-w-[900px]">
                  {selectedProvider.sections
                    .find((section) => section.id === effectiveSectionId)
                    ?.render() ?? <ProviderOverview provider={selectedProvider} />}
                </div>
              ) : (
                <div className="niuu-max-w-[720px]">
                  <ProviderOverview provider={selectedProvider} />
                </div>
              )
            ) : !selectedProvider.baseUrl ? (
              <div className="niuu-max-w-[720px] niuu-rounded-md niuu-border niuu-border-dashed niuu-border-border niuu-p-6">
                <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-2">
                  Settings endpoint not configured
                </p>
                <p className="niuu-text-sm niuu-text-text-secondary">
                  Configure a live service base for this plugin to mount its settings.
                </p>
              </div>
            ) : remoteSchemaQuery.isLoading ? (
              <div className="niuu-max-w-[720px] niuu-rounded-md niuu-border niuu-border-border niuu-p-6 niuu-text-sm niuu-text-text-secondary">
                Loading mounted settings…
              </div>
            ) : remoteSchemaQuery.isError || !remoteSchema || !remoteProviderForRender ? (
              <div className="niuu-max-w-[720px] niuu-rounded-md niuu-border niuu-border-dashed niuu-border-border niuu-p-6">
                <p className="niuu-text-sm niuu-font-medium niuu-text-text-primary niuu-mb-2">
                  Settings endpoint not available yet
                </p>
                <p className="niuu-text-sm niuu-text-text-secondary">
                  The unified settings shell is ready for this provider, but{' '}
                  <code>{selectedProvider.baseUrl}/settings</code> is not responding with a mounted
                  settings schema yet.
                </p>
              </div>
            ) : effectiveSectionId ? (
              <div className="niuu-max-w-[900px]">
                <RemoteSettingsSection
                  provider={remoteProviderForRender}
                  schema={remoteSchema}
                  sectionId={effectiveSectionId}
                />
              </div>
            ) : (
              <div className="niuu-max-w-[720px]">
                <ProviderOverview provider={selectedProvider} remoteSections={remoteSections} />
              </div>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
