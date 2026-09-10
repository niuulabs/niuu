import { Field, Select, Modal, Tooltip, TooltipProvider } from '@niuulabs/ui';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { IMimirService } from '../ports';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import './RegistryPage.css';
import { InstanceDeployments } from './InstanceDeployments';
import { useState } from 'react';
import {
  useCreateRegistryMount,
  useDeleteRegistryMount,
  useRegistryMounts,
  useUpdateRegistryMount,
} from '../application/useRegistryMounts';
import type { RegistryMount } from '../domain/registry';
import { accessScopeLabel } from '../domain/access-scope';

const INPUT_CLS =
  'niuu:w-full niuu:py-2 niuu:px-3 niuu:bg-bg-secondary niuu:border niuu:border-solid niuu:border-border ' +
  'niuu:rounded-md niuu:text-text-primary niuu:font-sans niuu:text-sm niuu:outline-none niuu:box-border ' +
  'niuu:focus:border-brand';

const LABEL_CLS =
  'niuu:text-[10px] niuu:uppercase niuu:tracking-wider niuu:text-text-muted niuu:block niuu:mb-1';

const EMPTY_REGISTRY_MOUNT: Omit<RegistryMount, 'id'> = {
  name: '',
  kind: 'remote',
  lifecycle: 'registered',
  role: 'shared',
  url: '',
  path: '',
  categories: [],
  authRef: null,
  defaultReadPriority: 10,
  enabled: true,
  healthStatus: 'unknown',
  healthMessage: '',
  desc: '',
};

function csvToCategories(value: string): string[] {
  return value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function categoriesToCsv(value: string[] | null): string {
  return (value ?? []).join(', ');
}

interface RegistryMountEditorProps {
  heading: string;
  mount: Omit<RegistryMount, 'id'>;
  submitLabel: string;
  isPending: boolean;
  onChange: (mount: Omit<RegistryMount, 'id'>) => void;
  onSubmit: () => void;
  onReset?: () => void;
}

function RegistryMountEditor({
  heading,
  mount,
  submitLabel,
  isPending,
  onChange,
  onSubmit,
  onReset,
}: RegistryMountEditorProps) {
  return (
    <section className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg">
      <div className="niuu:flex niuu:items-baseline niuu:justify-between niuu:mb-4">
        <h3 className="niuu:m-0 niuu:text-base niuu:text-text-primary">{heading}</h3>
        <span className="niuu:text-xs niuu:font-mono niuu:text-text-muted">
          Connection settings
        </span>
      </div>
      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3">
        <label>
          <span className={LABEL_CLS}>Name</span>
          <input
            className={INPUT_CLS}
            value={mount.name}
            onChange={(event) => onChange({ ...mount, name: event.currentTarget.value })}
          />
        </label>
        <Field label="Role">
          <Select
            value={mount.role}
            onValueChange={(value) => onChange({ ...mount, role: value as RegistryMount['role'] })}
            options={['local', 'shared', 'domain'].map((value) => ({ value, label: value }))}
          />
        </Field>
        <Field label="Kind">
          <Select
            value={mount.kind}
            onValueChange={(value) => onChange({ ...mount, kind: value as RegistryMount['kind'] })}
            options={['remote', 'local'].map((value) => ({ value, label: value }))}
          />
        </Field>
        <Field label="Lifecycle">
          <Select
            value={mount.lifecycle}
            onValueChange={(value) =>
              onChange({ ...mount, lifecycle: value as RegistryMount['lifecycle'] })
            }
            options={['registered', 'ephemeral'].map((value) => ({ value, label: value }))}
          />
        </Field>
        <label>
          <span className={LABEL_CLS}>Knowledge API URL</span>
          <input
            className={INPUT_CLS}
            value={mount.url}
            onChange={(event) => onChange({ ...mount, url: event.currentTarget.value })}
          />
        </label>
        <label>
          <span className={LABEL_CLS}>Path</span>
          <input
            className={INPUT_CLS}
            value={mount.path}
            onChange={(event) => onChange({ ...mount, path: event.currentTarget.value })}
          />
        </label>
        <label>
          <span className={LABEL_CLS}>Categories</span>
          <input
            className={INPUT_CLS}
            value={categoriesToCsv(mount.categories)}
            onChange={(event) =>
              onChange({ ...mount, categories: csvToCategories(event.currentTarget.value) })
            }
          />
        </label>
        <label>
          <span className={LABEL_CLS}>Auth ref</span>
          <input
            className={INPUT_CLS}
            value={mount.authRef ?? ''}
            onChange={(event) => onChange({ ...mount, authRef: event.currentTarget.value || null })}
          />
        </label>
        <label>
          <span className={LABEL_CLS}>Read priority</span>
          <input
            className={INPUT_CLS}
            type="number"
            value={mount.defaultReadPriority}
            onChange={(event) =>
              onChange({
                ...mount,
                defaultReadPriority: Number(event.currentTarget.value || 0),
              })
            }
          />
        </label>
        <Field label="Health status">
          <Select
            value={mount.healthStatus}
            onValueChange={(value) =>
              onChange({ ...mount, healthStatus: value as RegistryMount['healthStatus'] })
            }
            options={['unknown', 'healthy', 'degraded', 'down'].map((value) => ({
              value,
              label: value,
            }))}
          />
        </Field>
        <label className="niuu:flex niuu:items-end">
          <span className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-secondary">
            <input
              type="checkbox"
              checked={mount.enabled}
              onChange={(event) => onChange({ ...mount, enabled: event.currentTarget.checked })}
            />
            enabled
          </span>
        </label>
      </div>
      <label className="niuu:block niuu:mt-3">
        <span className={LABEL_CLS}>Description</span>
        <textarea
          className={`${INPUT_CLS} niuu:min-h-[5rem] niuu:resize-y`}
          value={mount.desc}
          onChange={(event) => onChange({ ...mount, desc: event.currentTarget.value })}
        />
      </label>
      <label className="niuu:block niuu:mt-3">
        <span className={LABEL_CLS}>Health message</span>
        <textarea
          className={`${INPUT_CLS} niuu:min-h-[3rem] niuu:resize-y`}
          value={mount.healthMessage}
          onChange={(event) => onChange({ ...mount, healthMessage: event.currentTarget.value })}
        />
      </label>
      <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:mt-4">
        <button
          type="button"
          className="niuu:py-1.5 niuu:px-3 niuu:bg-brand niuu:border niuu:border-solid niuu:border-brand niuu:rounded-md niuu:text-bg-primary niuu:font-sans niuu:text-xs niuu:font-medium niuu:cursor-pointer"
          onClick={onSubmit}
          disabled={isPending}
        >
          {submitLabel}
        </button>
        {onReset && (
          <button
            type="button"
            className="niuu:py-1.5 niuu:px-3 niuu:bg-bg-secondary niuu:border niuu:border-solid niuu:border-border niuu:rounded-md niuu:text-text-primary niuu:font-sans niuu:text-xs niuu:cursor-pointer"
            onClick={onReset}
            disabled={isPending}
          >
            Cancel
          </button>
        )}
      </div>
    </section>
  );
}

export function RegistryPage() {
  const ctx = usePluginCtx();
  const client = useQueryClient();
  const [deleting, setDeleting] = useState<{
    name: string;
    target?: string;
    connectionId?: string;
  } | null>(null);
  const removeInstance = useMutation({
    mutationFn: async () => {
      if (!deleting) return;
      await mounts.controlDeployment!(deleting.name, 'delete', deleting.target);
      if (deleting.connectionId) await mounts.deleteRegistryMount!(deleting.connectionId);
    },
    onSuccess: () => {
      if (ctx.tweaks['activeMount'] === deleting?.name) ctx.setTweak('activeMount', '');
      setDeleting(null);
      void client.invalidateQueries({ queryKey: ['mimir'] });
    },
  });
  const { mounts } = useService<IMimirService>('mimir');
  const live = useQuery({ queryKey: ['mimir', 'mounts'], queryFn: () => mounts.listMounts() });
  const deployments = useQuery({
    queryKey: ['mimir', 'deployments'],
    queryFn: () => mounts.getDeployments!(),
    enabled: !!mounts.getDeployments,
    refetchInterval: 10000,
  });
  const [deploying, setDeploying] = useState(false);
  const { data: registryMounts = [], isLoading, error } = useRegistryMounts();
  const createMount = useCreateRegistryMount();
  const updateMount = useUpdateRegistryMount();
  const deleteMount = useDeleteRegistryMount();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<Omit<RegistryMount, 'id'>>(EMPTY_REGISTRY_MOUNT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingDraft, setEditingDraft] = useState<Omit<RegistryMount, 'id'>>(EMPTY_REGISTRY_MOUNT);

  // Mount names are the routing identity shared by connections and deployments.
  const names = [
    ...new Set([
      ...registryMounts.map((m) => m.name),
      ...(live.data ?? []).map((m) => m.name),
      ...(deployments.data?.releases ?? []).map((r) => r.name),
    ]),
  ];
  const instances = names.flatMap((name) => {
    const connection = registryMounts.find(
      (m) => m.name === name && !m.id.startsWith('deployment:'),
    );
    const runtime = live.data?.find((m) => m.name === name);
    const releases = deployments.data?.releases.filter((r) => r.name === name) ?? [];
    // Names can repeat across Guild targets; never merge those deployments together.
    const choices: ((typeof releases)[number] | undefined)[] = releases.length
      ? [...releases]
      : [undefined];
    if (releases.length > 1 && (connection || runtime)) choices.unshift(undefined);
    return choices.map((release) => {
      const attached = releases.length > 1 && release ? undefined : connection;
      const mounted = releases.length > 1 && release ? undefined : runtime;
      const target = deployments.data?.targets?.find((t) => t.id === release?.target);
      return {
        ...EMPTY_REGISTRY_MOUNT,
        ...attached,
        name,
        id: attached?.id ?? (release ? `${release.target ?? ''}/${name}` : name),
        healthStatus:
          mounted?.status ?? attached?.healthStatus ?? (release?.ready ? 'healthy' : 'unknown'),
        url: attached?.url || mounted?.url || '',
        desc: attached?.desc || mounted?.desc || '',
        defaultReadPriority: attached?.defaultReadPriority ?? mounted?.priority ?? 10,
        pages: mounted?.pages,
        accessScope: mounted?.accessScope ?? attached?.accessScope ?? release?.access_scope,
        connection: attached,
        hasMount: !!mounted || !!attached,
        release,
        targetLabel: target?.source_name
          ? `${target.source_name} · ${target.cluster}`
          : release?.target,
      };
    });
  });
  const activeCount = instances.filter((m) => m.enabled).length;

  if (isLoading) {
    return <div className="niuu:p-6 niuu:text-sm niuu:text-text-muted">loading registry…</div>;
  }

  if (error) {
    return (
      <div className="niuu:p-6">
        <div className="niuu:text-xs niuu:text-critical niuu:bg-critical-bg niuu:border niuu:border-critical-bo niuu:rounded-sm niuu:px-4 niuu:py-2">
          {error instanceof Error ? error.message : String(error)}
        </div>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <div className="knowledge-registry" data-testid="registry-page">
        <div className="niuu:flex niuu:items-baseline niuu:justify-between niuu:mb-6">
          <div>
            <h2 className="niuu:m-0 niuu:text-xl niuu:text-text-primary">Knowledge registry</h2>
            <p className="niuu:m-0 niuu:mt-1 niuu:text-sm niuu:text-text-secondary">
              A home for your knowledge. Create an instance or connect one you already use.
            </p>
          </div>
          <div className="niuu:font-mono niuu:text-xs niuu:text-text-muted">
            {instances.length} instances · {activeCount} enabled
          </div>
        </div>

        <div className="registry-section-heading">
          <div>
            <span className="registry-eyebrow">Your library</span>
            <h3>Instances</h3>
          </div>
          <div className="niuu:flex niuu:gap-3">
            <button
              className="registry-primary"
              type="button"
              aria-expanded={deploying}
              onClick={() => {
                setDeploying(!deploying);
                setAdding(false);
              }}
            >
              + Deploy instance
            </button>
            <button
              className="registry-primary"
              type="button"
              aria-expanded={adding}
              onClick={() => {
                setAdding(!adding);
                setDeploying(false);
              }}
            >
              + Add connection
            </button>
          </div>
        </div>
        <Modal
          open={deploying}
          onOpenChange={setDeploying}
          title="Deploy instance"
          className="registry-modal registry-deploy-modal"
        >
          {deploying && <InstanceDeployments />}
        </Modal>
        <Modal
          open={adding}
          onOpenChange={setAdding}
          title="Add connection"
          className="registry-modal"
        >
          <div className="registry-connection-form">
            <p>Connect an existing knowledge API. Native gbrain endpoints require its adapter.</p>
            <RegistryMountEditor
              heading="Register an existing connection"
              mount={draft}
              submitLabel="Create"
              isPending={createMount.isPending}
              onChange={setDraft}
              onSubmit={() =>
                createMount.mutate(draft, {
                  onSuccess: () => {
                    setDraft(EMPTY_REGISTRY_MOUNT);
                    setAdding(false);
                  },
                })
              }
              onReset={() => setAdding(false)}
            />
            {createMount.error && <p role="alert">{createMount.error.message}</p>}
          </div>
        </Modal>
        <Modal
          open={!!deleting}
          onOpenChange={(open) => {
            if (!open && !removeInstance.isPending) setDeleting(null);
          }}
          title="Delete local instance"
          className="registry-modal"
          actions={[
            { label: 'Cancel', disabled: removeInstance.isPending },
            {
              label: removeInstance.isPending ? 'Deleting…' : 'Delete instance',
              variant: 'destructive',
              closes: false,
              disabled: removeInstance.isPending,
              onClick: () => removeInstance.mutate(),
            },
          ]}
        >
          <p>
            Permanently delete <strong>{deleting?.name}</strong> and its local data? Its service and
            attached maintenance will also be removed.
          </p>
          {removeInstance.error && <p role="alert">{removeInstance.error.message}</p>}
        </Modal>
        {(live.error ||
          deployments.error ||
          createMount.error ||
          updateMount.error ||
          deleteMount.error) && (
          <p role="alert">
            {
              (
                live.error ||
                deployments.error ||
                createMount.error ||
                updateMount.error ||
                deleteMount.error
              )?.message
            }
          </p>
        )}
        <div className="registry-connections">
          <section className="niuu:flex niuu:flex-col niuu:gap-3">
            {instances.map((mount) => {
              const isEditing = editingId === mount.id;
              return (
                <article
                  key={mount.id}
                  className="niuu:p-4 niuu:bg-bg-secondary niuu:border niuu:border-border-subtle niuu:rounded-lg"
                >
                  <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-4">
                    <div>
                      <div className="niuu:flex niuu:items-center niuu:gap-2">
                        <span className="niuu:text-base niuu:text-text-primary niuu:font-semibold">
                          {mount.name}
                        </span>
                        <span className="niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                          {accessScopeLabel(mount.accessScope)} ·{' '}
                          {mount.release
                            ? `${mount.release.backend} · Managed · ${mount.targetLabel ?? 'cluster'}`
                            : 'Connected'}
                        </span>
                      </div>
                      <div className="niuu:font-mono niuu:text-[11px] niuu:text-text-muted niuu:mt-1">
                        {mount.url || mount.path || 'Configured by host'}
                      </div>
                      <p className="niuu:m-0 niuu:mt-2 niuu:text-sm niuu:text-text-secondary">
                        {mount.desc || 'No description provided.'}
                      </p>
                    </div>
                    <div className="niuu:flex niuu:gap-2">
                      <button
                        type="button"
                        className="registry-primary"
                        onClick={() => {
                          ctx.setTweak(
                            'mimir.deployment',
                            mount.release && !mount.hasMount
                              ? { name: mount.name, target: mount.release.target }
                              : null,
                          );
                          ctx.setTweak('activeMount', mount.hasMount ? mount.name : 'all');
                          ctx.setTweak('mimir.registryView', 'Analytics');
                        }}
                      >
                        Inspect
                      </button>
                      {(!mount.connection || mount.release?.can_delete) && (
                        <button
                          className="registry-destructive"
                          type="button"
                          disabled={!mount.release?.can_delete || !mounts.controlDeployment}
                          onClick={() => {
                            removeInstance.reset();
                            setDeleting({
                              name: mount.name,
                              target: mount.release?.target,
                              connectionId: mount.connection?.id,
                            });
                          }}
                        >
                          Delete
                        </button>
                      )}
                      {mount.connection && (
                        <>
                          <button
                            type="button"
                            className="niuu:py-1.5 niuu:px-3 niuu:bg-bg-secondary niuu:border niuu:border-solid niuu:border-border niuu:rounded-md niuu:text-text-primary niuu:font-sans niuu:text-xs niuu:cursor-pointer"
                            onClick={() => {
                              setEditingId(mount.id);
                              setEditingDraft({
                                name: mount.name,
                                kind: mount.kind,
                                lifecycle: mount.lifecycle,
                                role: mount.role,
                                url: mount.url,
                                path: mount.path,
                                categories: mount.categories ?? [],
                                adapter: mount.adapter,
                                kwargs: mount.kwargs,
                                secretKwargsEnv: mount.secretKwargsEnv,
                                authRef: mount.authRef ?? null,
                                defaultReadPriority: mount.defaultReadPriority,
                                enabled: mount.enabled,
                                healthStatus: mount.healthStatus,
                                healthMessage: mount.healthMessage,
                                desc: mount.desc,
                              });
                            }}
                          >
                            Edit
                          </button>
                          {!mount.release?.can_delete && (
                            <Tooltip content="Remove connection">
                              <button
                                aria-label="Remove connection"
                                type="button"
                                className="registry-destructive"
                                onClick={() => deleteMount.mutate(mount.id)}
                                disabled={deleteMount.isPending}
                              >
                                Remove
                              </button>
                            </Tooltip>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="niuu:flex niuu:flex-wrap niuu:gap-2 niuu:mt-3 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                    <span>health: {mount.healthStatus}</span>
                    {mount.pages !== undefined && <span>{mount.pages} pages</span>}
                    {mount.release && (
                      <span>{mount.release.ready ? 'Ready' : mount.release.message}</span>
                    )}
                    <span>priority: {mount.defaultReadPriority}</span>
                    <span>{mount.enabled ? 'enabled' : 'disabled'}</span>
                    {(mount.categories ?? []).length > 0 && (
                      <span>categories: {mount.categories?.join(', ')}</span>
                    )}
                  </div>

                  {isEditing && (
                    <div className="niuu:mt-4">
                      <RegistryMountEditor
                        heading={`Edit ${mount.name}`}
                        mount={editingDraft}
                        submitLabel="Save"
                        isPending={updateMount.isPending}
                        onChange={setEditingDraft}
                        onSubmit={() =>
                          updateMount.mutate(
                            { id: mount.id, mount: editingDraft },
                            {
                              onSuccess: () => {
                                setEditingId(null);
                                setEditingDraft(EMPTY_REGISTRY_MOUNT);
                              },
                            },
                          )
                        }
                        onReset={() => {
                          setEditingId(null);
                          setEditingDraft(EMPTY_REGISTRY_MOUNT);
                        }}
                      />
                    </div>
                  )}
                </article>
              );
            })}

            {instances.length === 0 && (
              <div className="niuu:p-6 niuu:border niuu:border-dashed niuu:border-border-subtle niuu:rounded-lg niuu:text-sm niuu:text-text-muted">
                <span className="registry-empty-icon" aria-hidden="true">
                  ◇
                </span>
                <h4>Your library is ready for its first connection.</h4>
                <p>Add an existing knowledge instance to make it available to your runtimes.</p>
              </div>
            )}
          </section>
        </div>
      </div>
    </TooltipProvider>
  );
}
