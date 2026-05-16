import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createApiClient } from '@niuulabs/query';
import { useConfig, useService, type IIdentityService } from '@niuulabs/plugin-sdk';
import { resolveSharedApiBase } from './services';

type InstanceKind = 'volundr' | 'mimir' | 'bifrost' | 'ravn' | 'observatory' | 'generic';

type InstanceRecord = {
  id: string;
  kind: InstanceKind;
  slug: string;
  name: string;
  baseUrl: string;
  visibility: string;
  ownerId: string | null;
  tenantId: string | null;
  enabled: boolean;
  isDefault: boolean;
  config: Record<string, unknown>;
};

type InstanceTestResult = {
  ok: boolean;
  statusCode?: number | null;
  message: string;
};

const KIND_OPTIONS: Array<{ value: InstanceKind; label: string }> = [
  { value: 'volundr', label: 'Volundr' },
  { value: 'mimir', label: 'Mimir' },
  { value: 'bifrost', label: 'Bifrost' },
  { value: 'ravn', label: 'Ravn' },
  { value: 'observatory', label: 'Observatory' },
  { value: 'generic', label: 'Generic API' },
];

function scopeLabel(instance: InstanceRecord): string {
  if (instance.visibility === 'tenant') return `Tenant: ${instance.tenantId ?? 'unknown'}`;
  if (instance.visibility === 'user') return `User: ${instance.ownerId ?? 'unknown'}`;
  return 'System';
}

function kindLabel(kind: InstanceKind): string {
  return KIND_OPTIONS.find((option) => option.value === kind)?.label ?? kind;
}

function openPathFor(instance: InstanceRecord): string | null {
  if (instance.kind === 'volundr') {
    return '/volundr?config=/config.live.json';
  }
  return null;
}

function InstanceCard({
  client,
  instance,
}: {
  client: ReturnType<typeof createApiClient>;
  instance: InstanceRecord;
}) {
  const testMutation = useMutation({
    mutationFn: () => client.post<InstanceTestResult>(`/niuu/instances/${instance.id}/test`),
  });
  const openPath = openPathFor(instance);

  return (
    <article className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-primary niuu-p-5 niuu-space-y-4">
      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4">
        <div className="niuu-min-w-0">
          <div className="niuu-flex niuu-flex-wrap niuu-items-center niuu-gap-2">
            <h3 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
              {instance.name}
            </h3>
            <span className="niuu-rounded-full niuu-border niuu-border-border niuu-px-2 niuu-py-0.5 niuu-text-[10px] niuu-font-mono niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-muted">
              {kindLabel(instance.kind)}
            </span>
            {instance.isDefault ? (
              <span className="niuu-rounded-full niuu-bg-brand/10 niuu-px-2 niuu-py-0.5 niuu-text-[10px] niuu-font-mono niuu-uppercase niuu-tracking-[0.12em] niuu-text-brand-200">
                Default
              </span>
            ) : null}
          </div>
          <p className="niuu-mt-1 niuu-text-xs niuu-font-mono niuu-text-text-secondary">
            {instance.baseUrl}
          </p>
        </div>
        <div className="niuu-flex niuu-gap-2">
          {openPath ? (
            <a
              href={openPath}
              className="niuu-rounded-md niuu-border niuu-border-border niuu-px-3 niuu-py-1.5 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
            >
              Open
            </a>
          ) : null}
          <button
            type="button"
            onClick={() => testMutation.mutate()}
            className="niuu-rounded-md niuu-border niuu-border-border niuu-px-3 niuu-py-1.5 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
          >
            Test
          </button>
        </div>
      </div>
      <div className="niuu-grid niuu-grid-cols-2 lg:niuu-grid-cols-4 niuu-gap-3">
        <div className="niuu-rounded-lg niuu-bg-bg-secondary niuu-p-3">
          <p className="niuu-text-xs niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
            Scope
          </p>
          <p className="niuu-mt-1 niuu-text-sm niuu-text-text-primary">{scopeLabel(instance)}</p>
        </div>
        <div className="niuu-rounded-lg niuu-bg-bg-secondary niuu-p-3">
          <p className="niuu-text-xs niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
            Slug
          </p>
          <p className="niuu-mt-1 niuu-text-sm niuu-text-text-primary">{instance.slug}</p>
        </div>
        <div className="niuu-rounded-lg niuu-bg-bg-secondary niuu-p-3">
          <p className="niuu-text-xs niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
            State
          </p>
          <p className="niuu-mt-1 niuu-text-sm niuu-text-text-primary">
            {instance.enabled ? 'Enabled' : 'Disabled'}
          </p>
        </div>
        <div className="niuu-rounded-lg niuu-bg-bg-secondary niuu-p-3">
          <p className="niuu-text-xs niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
            Health
          </p>
          <p className="niuu-mt-1 niuu-text-sm niuu-text-text-primary">
            {testMutation.data
              ? testMutation.data.ok
                ? 'Reachable'
                : 'Unreachable'
              : 'Not checked'}
          </p>
        </div>
      </div>
      <p className="niuu-text-sm niuu-text-text-secondary">
        Guild tracks where this runtime lives, who can see it, and whether the shared shell can
        reach it. Day-to-day sessions stay in the product UI for that runtime.
      </p>
      {testMutation.data ? (
        <p
          className={`niuu-text-sm ${testMutation.data.ok ? 'niuu-text-emerald-400' : 'niuu-text-rose-400'}`}
        >
          {testMutation.data.message}
        </p>
      ) : null}
    </article>
  );
}

export function GuildPage() {
  const config = useConfig();
  const identity = useService<IIdentityService>('identity');
  const queryClient = useQueryClient();
  const sharedBase = resolveSharedApiBase(config);
  const client = useMemo(() => {
    if (!sharedBase) return null;
    return createApiClient(sharedBase);
  }, [sharedBase]);
  const [form, setForm] = useState<{
    kind: InstanceKind;
    name: string;
    slug: string;
    baseUrl: string;
    visibility: 'user' | 'tenant' | 'system';
  }>({
    kind: 'volundr',
    name: '',
    slug: '',
    baseUrl: '',
    visibility: 'user',
  });

  const identityQuery = useQuery({
    queryKey: ['guild-identity'],
    queryFn: () => identity.getIdentity(),
  });
  const instancesQuery = useQuery({
    queryKey: ['guild-instances'],
    enabled: client != null,
    queryFn: () => client!.get<InstanceRecord[]>('/niuu/instances'),
    refetchInterval: 5_000,
  });

  const createMutation = useMutation({
    mutationFn: (payload: typeof form & { tenantId?: string }) =>
      client!.post<InstanceRecord>('/niuu/instances', payload),
    onSuccess: async () => {
      setForm({
        kind: 'volundr',
        name: '',
        slug: '',
        baseUrl: '',
        visibility: 'user',
      });
      await queryClient.invalidateQueries({ queryKey: ['guild-instances'] });
    },
  });

  if (!client) {
    return (
      <div className="niuu-p-6">
        <p className="niuu-text-sm niuu-text-text-secondary">
          The shared Niuu API is not configured, so Guild targets are unavailable.
        </p>
      </div>
    );
  }

  const currentIdentity = identityQuery.data;
  const canCreateSharedScope = currentIdentity?.roles.includes('volundr:admin') ?? false;

  return (
    <div className="niuu-p-6 niuu-space-y-6">
      <section className="niuu-space-y-2">
        <h1 className="niuu-text-2xl niuu-font-semibold niuu-text-text-primary">Guild</h1>
        <p className="niuu-max-w-3xl niuu-text-sm niuu-text-text-secondary">
          Register runtime endpoints, define who can access them, and validate that the shared
          shell can reach them. Guild is the catalog and control surface; the product UI is where
          people operate sessions.
        </p>
      </section>

      <section className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-primary niuu-p-5 niuu-space-y-4">
        <div>
          <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
            Register Endpoint
          </h2>
          <p className="niuu-text-sm niuu-text-text-secondary">
            Add any Niuu-compatible API endpoint. Config-seeded entries appear here too.
          </p>
        </div>
        <form
          className="niuu-grid niuu-grid-cols-1 lg:niuu-grid-cols-5 niuu-gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            createMutation.mutate({
              ...form,
              tenantId: form.visibility === 'tenant' ? currentIdentity?.tenantId : undefined,
            });
          }}
        >
          <select
            value={form.kind}
            onChange={(event) =>
              setForm((current) => ({ ...current, kind: event.target.value as InstanceKind }))
            }
            className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-2 niuu-text-sm"
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <input
            value={form.name}
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            placeholder="Display name"
            className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-2 niuu-text-sm"
          />
          <input
            value={form.slug}
            onChange={(event) => setForm((current) => ({ ...current, slug: event.target.value }))}
            placeholder="slug"
            className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-2 niuu-text-sm"
          />
          <input
            value={form.baseUrl}
            onChange={(event) =>
              setForm((current) => ({ ...current, baseUrl: event.target.value }))
            }
            placeholder="http://127.0.0.1:8181"
            className="niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-2 niuu-text-sm"
          />
          <div className="niuu-flex niuu-gap-3">
            <select
              value={form.visibility}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  visibility: event.target.value as 'user' | 'tenant' | 'system',
                }))
              }
              className="niuu-flex-1 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-2 niuu-text-sm"
            >
              <option value="user">User</option>
              <option value="tenant" disabled={!canCreateSharedScope}>
                Tenant
              </option>
              <option value="system" disabled={!canCreateSharedScope}>
                System
              </option>
            </select>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="niuu-rounded-md niuu-bg-text-primary niuu-px-4 niuu-py-2 niuu-text-sm niuu-font-medium niuu-text-bg-primary"
            >
              Save
            </button>
          </div>
        </form>
        {createMutation.error ? (
          <p className="niuu-text-sm niuu-text-rose-400">
            {createMutation.error instanceof Error
              ? createMutation.error.message
              : 'Failed to create instance.'}
          </p>
        ) : null}
      </section>

      <section className="niuu-space-y-4">
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <div>
            <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
              Visible Endpoints
            </h2>
            <p className="niuu-text-sm niuu-text-text-secondary">
              Backends visible to the current user and tenant context.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void instancesQuery.refetch()}
            className="niuu-rounded-md niuu-border niuu-border-border niuu-px-3 niuu-py-1.5 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
          >
            Refresh
          </button>
        </div>
        {instancesQuery.error ? (
          <div className="niuu-rounded-xl niuu-border niuu-border-rose-500/40 niuu-bg-rose-500/5 niuu-p-4 niuu-text-sm niuu-text-rose-300">
            {instancesQuery.error instanceof Error
              ? instancesQuery.error.message
              : 'Failed to load visible endpoints.'}
          </div>
        ) : null}
        <div className="niuu-grid niuu-grid-cols-1 xl:niuu-grid-cols-2 niuu-gap-4">
          {(instancesQuery.data ?? []).map((instance) => (
            <InstanceCard key={instance.id} client={client} instance={instance} />
          ))}
        </div>
        {!instancesQuery.isLoading && (instancesQuery.data?.length ?? 0) === 0 ? (
          <div className="niuu-rounded-xl niuu-border niuu-border-dashed niuu-border-border niuu-p-6 niuu-text-sm niuu-text-text-secondary">
            No endpoints are visible yet.
          </div>
        ) : null}
      </section>
    </div>
  );
}
