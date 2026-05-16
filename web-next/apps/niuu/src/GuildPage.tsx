import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createApiClient } from '@niuulabs/query';
import { useConfig, useService, type IIdentityService } from '@niuulabs/plugin-sdk';
import { resolveSharedApiBase } from './services';

type InstanceRecord = {
  id: string;
  kind: string;
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

type InstanceSession = {
  id: string;
  name: string;
  status: string;
  model?: string | null;
  ownerId?: string | null;
  tenantId?: string | null;
};

function scopeLabel(instance: InstanceRecord): string {
  if (instance.visibility === 'tenant') return `Tenant: ${instance.tenantId ?? 'unknown'}`;
  if (instance.visibility === 'user') return `User: ${instance.ownerId ?? 'unknown'}`;
  return 'System';
}

function InstanceCard({
  client,
  instance,
}: {
  client: ReturnType<typeof createApiClient>;
  instance: InstanceRecord;
}) {
  const sessionsQuery = useQuery({
    queryKey: ['guild-instance-sessions', instance.id],
    queryFn: () => client.get<InstanceSession[]>(`/niuu/instances/${instance.id}/sessions`),
    refetchInterval: 5_000,
  });
  const testMutation = useMutation({
    mutationFn: () => client.post<InstanceTestResult>(`/niuu/instances/${instance.id}/test`),
  });

  const sessions = sessionsQuery.data ?? [];
  const stopped = sessions.filter((session) =>
    ['stopped', 'archived', 'failed'].includes(session.status),
  ).length;

  return (
    <article className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-primary niuu-p-5 niuu-space-y-4">
      <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4">
        <div>
          <h3 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">{instance.name}</h3>
          <p className="niuu-text-xs niuu-font-mono niuu-text-text-secondary">{instance.baseUrl}</p>
        </div>
        <button
          type="button"
          onClick={() => testMutation.mutate()}
          className="niuu-rounded-md niuu-border niuu-border-border niuu-px-3 niuu-py-1.5 niuu-text-sm niuu-text-text-primary hover:niuu-bg-bg-secondary"
        >
          Test
        </button>
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
            Sessions
          </p>
          <p className="niuu-mt-1 niuu-text-sm niuu-text-text-primary">{sessions.length}</p>
        </div>
        <div className="niuu-rounded-lg niuu-bg-bg-secondary niuu-p-3">
          <p className="niuu-text-xs niuu-uppercase niuu-tracking-[0.14em] niuu-text-text-muted">
            Stopped
          </p>
          <p className="niuu-mt-1 niuu-text-sm niuu-text-text-primary">{stopped}</p>
        </div>
      </div>
      {testMutation.data ? (
        <p
          className={`niuu-text-sm ${testMutation.data.ok ? 'niuu-text-emerald-400' : 'niuu-text-rose-400'}`}
        >
          {testMutation.data.message}
        </p>
      ) : null}
      <div className="niuu-overflow-hidden niuu-rounded-lg niuu-border niuu-border-border">
        <table className="niuu-min-w-full niuu-text-sm">
          <thead className="niuu-bg-bg-secondary">
            <tr>
              <th className="niuu-px-3 niuu-py-2 niuu-text-left niuu-text-text-muted">Session</th>
              <th className="niuu-px-3 niuu-py-2 niuu-text-left niuu-text-text-muted">Status</th>
              <th className="niuu-px-3 niuu-py-2 niuu-text-left niuu-text-text-muted">Owner</th>
            </tr>
          </thead>
          <tbody>
            {sessions.slice(0, 8).map((session) => (
              <tr key={session.id} className="niuu-border-t niuu-border-border">
                <td className="niuu-px-3 niuu-py-2 niuu-text-text-primary">{session.name}</td>
                <td className="niuu-px-3 niuu-py-2 niuu-text-text-secondary">{session.status}</td>
                <td className="niuu-px-3 niuu-py-2 niuu-text-text-secondary">
                  {session.ownerId ?? session.tenantId ?? 'n/a'}
                </td>
              </tr>
            ))}
            {sessions.length === 0 ? (
              <tr>
                <td
                  colSpan={3}
                  className="niuu-px-3 niuu-py-4 niuu-text-center niuu-text-text-secondary"
                >
                  No sessions reported by this instance yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
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
  const [form, setForm] = useState({
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
    queryFn: () => client!.get<InstanceRecord[]>('/niuu/instances?kind=volundr'),
    refetchInterval: 5_000,
  });

  const createMutation = useMutation({
    mutationFn: (payload: typeof form & { tenantId?: string }) =>
      client!.post<InstanceRecord>('/niuu/instances', payload),
    onSuccess: async () => {
      setForm({ name: '', slug: '', baseUrl: '', visibility: 'user' });
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
  const canCreateTenantScope = currentIdentity?.roles.includes('volundr:admin') ?? false;

  return (
    <div className="niuu-p-6 niuu-space-y-6">
      <section className="niuu-space-y-2">
        <h1 className="niuu-text-2xl niuu-font-semibold niuu-text-text-primary">Guild</h1>
        <p className="niuu-max-w-3xl niuu-text-sm niuu-text-text-secondary">
          Register multiple Volundr instances, validate them from the shared shell, and inspect the
          sessions each one is carrying. This is the multi-instance control surface that Ting uses
          for explicit target selection.
        </p>
      </section>

      <section className="niuu-rounded-xl niuu-border niuu-border-border niuu-bg-bg-primary niuu-p-5 niuu-space-y-4">
        <div>
          <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">
            Register Instance
          </h2>
          <p className="niuu-text-sm niuu-text-text-secondary">
            Add a Volundr instance through the UI. Config-seeded instances show up here too.
          </p>
        </div>
        <form
          className="niuu-grid niuu-grid-cols-1 lg:niuu-grid-cols-4 niuu-gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            createMutation.mutate({
              ...form,
              tenantId: form.visibility === 'tenant' ? currentIdentity?.tenantId : undefined,
            });
          }}
        >
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
                setForm((current) => ({ ...current, visibility: event.target.value }))
              }
              className="niuu-flex-1 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-2 niuu-text-sm"
            >
              <option value="user">User</option>
              <option value="tenant" disabled={!canCreateTenantScope}>
                Tenant
              </option>
              <option value="system" disabled={!canCreateTenantScope}>
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
              Visible Instances
            </h2>
            <p className="niuu-text-sm niuu-text-text-secondary">
              Targets available to the current identity and tenant context.
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
              : 'Failed to load visible instances.'}
          </div>
        ) : null}
        <div className="niuu-grid niuu-grid-cols-1 xl:niuu-grid-cols-2 niuu-gap-4">
          {(instancesQuery.data ?? []).map((instance) => (
            <InstanceCard key={instance.id} client={client} instance={instance} />
          ))}
        </div>
        {!instancesQuery.isLoading && (instancesQuery.data?.length ?? 0) === 0 ? (
          <div className="niuu-rounded-xl niuu-border niuu-border-dashed niuu-border-border niuu-p-6 niuu-text-sm niuu-text-text-secondary">
            No Volundr instances are visible yet.
          </div>
        ) : null}
      </section>
    </div>
  );
}
