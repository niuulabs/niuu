import { useMemo, useState } from 'react';
import {
  useCreateRegistryMount,
  useDeleteRegistryMount,
  useRegistryMounts,
  useUpdateRegistryMount,
} from '../application/useRegistryMounts';
import type { RegistryMount } from '../domain/registry';

const INPUT_CLS =
  'niuu-w-full niuu-py-2 niuu-px-3 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border ' +
  'niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-sm niuu-outline-none niuu-box-border ' +
  'focus:niuu-border-brand';

const LABEL_CLS =
  'niuu-text-[10px] niuu-uppercase niuu-tracking-wider niuu-text-text-muted niuu-block niuu-mb-1';

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
    <section className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg">
      <div className="niuu-flex niuu-items-baseline niuu-justify-between niuu-mb-4">
        <h3 className="niuu-m-0 niuu-text-base niuu-text-text-primary">{heading}</h3>
        <span className="niuu-text-xs niuu-font-mono niuu-text-text-muted">
          registry-backed mount metadata
        </span>
      </div>
      <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
        <label>
          <span className={LABEL_CLS}>Name</span>
          <input
            className={INPUT_CLS}
            value={mount.name}
            onChange={(event) => onChange({ ...mount, name: event.currentTarget.value })}
          />
        </label>
        <label>
          <span className={LABEL_CLS}>Role</span>
          <select
            className={INPUT_CLS}
            value={mount.role}
            onChange={(event) =>
              onChange({
                ...mount,
                role: event.currentTarget.value as RegistryMount['role'],
              })
            }
          >
            <option value="local">local</option>
            <option value="shared">shared</option>
            <option value="domain">domain</option>
          </select>
        </label>
        <label>
          <span className={LABEL_CLS}>Kind</span>
          <select
            className={INPUT_CLS}
            value={mount.kind}
            onChange={(event) =>
              onChange({
                ...mount,
                kind: event.currentTarget.value as RegistryMount['kind'],
              })
            }
          >
            <option value="remote">remote</option>
            <option value="local">local</option>
          </select>
        </label>
        <label>
          <span className={LABEL_CLS}>Lifecycle</span>
          <select
            className={INPUT_CLS}
            value={mount.lifecycle}
            onChange={(event) =>
              onChange({
                ...mount,
                lifecycle: event.currentTarget.value as RegistryMount['lifecycle'],
              })
            }
          >
            <option value="registered">registered</option>
            <option value="ephemeral">ephemeral</option>
          </select>
        </label>
        <label>
          <span className={LABEL_CLS}>URL</span>
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
        <label>
          <span className={LABEL_CLS}>Health status</span>
          <select
            className={INPUT_CLS}
            value={mount.healthStatus}
            onChange={(event) =>
              onChange({
                ...mount,
                healthStatus: event.currentTarget.value as RegistryMount['healthStatus'],
              })
            }
          >
            <option value="unknown">unknown</option>
            <option value="healthy">healthy</option>
            <option value="degraded">degraded</option>
            <option value="down">down</option>
          </select>
        </label>
        <label className="niuu-flex niuu-items-end">
          <span className="niuu-flex niuu-items-center niuu-gap-2 niuu-text-sm niuu-text-text-secondary">
            <input
              type="checkbox"
              checked={mount.enabled}
              onChange={(event) => onChange({ ...mount, enabled: event.currentTarget.checked })}
            />
            enabled
          </span>
        </label>
      </div>
      <label className="niuu-block niuu-mt-3">
        <span className={LABEL_CLS}>Description</span>
        <textarea
          className={`${INPUT_CLS} niuu-min-h-[5rem] niuu-resize-y`}
          value={mount.desc}
          onChange={(event) => onChange({ ...mount, desc: event.currentTarget.value })}
        />
      </label>
      <label className="niuu-block niuu-mt-3">
        <span className={LABEL_CLS}>Health message</span>
        <textarea
          className={`${INPUT_CLS} niuu-min-h-[3rem] niuu-resize-y`}
          value={mount.healthMessage}
          onChange={(event) => onChange({ ...mount, healthMessage: event.currentTarget.value })}
        />
      </label>
      <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-mt-4">
        <button
          type="button"
          className="niuu-py-1.5 niuu-px-3 niuu-bg-brand niuu-border niuu-border-solid niuu-border-brand niuu-rounded-md niuu-text-bg-primary niuu-font-sans niuu-text-xs niuu-font-medium niuu-cursor-pointer"
          onClick={onSubmit}
          disabled={isPending}
        >
          {submitLabel}
        </button>
        {onReset && (
          <button
            type="button"
            className="niuu-py-1.5 niuu-px-3 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-xs niuu-cursor-pointer"
            onClick={onReset}
            disabled={isPending}
          >
            Reset
          </button>
        )}
      </div>
    </section>
  );
}

export function RegistryPage() {
  const { data: registryMounts = [], isLoading, error } = useRegistryMounts();
  const createMount = useCreateRegistryMount();
  const updateMount = useUpdateRegistryMount();
  const deleteMount = useDeleteRegistryMount();
  const [draft, setDraft] = useState<Omit<RegistryMount, 'id'>>(EMPTY_REGISTRY_MOUNT);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingDraft, setEditingDraft] = useState<Omit<RegistryMount, 'id'>>(EMPTY_REGISTRY_MOUNT);

  const activeCount = useMemo(
    () => registryMounts.filter((mount: RegistryMount) => mount.enabled).length,
    [registryMounts],
  );

  if (isLoading) {
    return <div className="niuu-p-6 niuu-text-sm niuu-text-text-muted">loading registry…</div>;
  }

  if (error) {
    return (
      <div className="niuu-p-6">
        <div className="niuu-text-xs niuu-text-critical niuu-bg-critical-bg niuu-border niuu-border-critical-bo niuu-rounded-sm niuu-px-4 niuu-py-2">
          {error instanceof Error ? error.message : String(error)}
        </div>
      </div>
    );
  }

  return (
    <div className="niuu-p-6 niuu-overflow-y-auto niuu-h-full" data-testid="registry-page">
      <div className="niuu-flex niuu-items-baseline niuu-justify-between niuu-mb-6">
        <div>
          <h2 className="niuu-m-0 niuu-text-xl niuu-text-text-primary">Registry</h2>
          <p className="niuu-m-0 niuu-mt-1 niuu-text-sm niuu-text-text-secondary">
            Known Mimir backends, separate from the mounts currently attached to a live runtime.
          </p>
        </div>
        <div className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
          {registryMounts.length} registered · {activeCount} enabled
        </div>
      </div>

      <div className="niuu-grid niuu-grid-cols-[1.3fr_1fr] niuu-gap-6">
        <section className="niuu-flex niuu-flex-col niuu-gap-3">
          {registryMounts.map((mount: RegistryMount) => {
            const isEditing = editingId === mount.id;
            return (
              <article
                key={mount.id}
                className="niuu-p-4 niuu-bg-bg-secondary niuu-border niuu-border-border-subtle niuu-rounded-lg"
              >
                <div className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4">
                  <div>
                    <div className="niuu-flex niuu-items-center niuu-gap-2">
                      <span className="niuu-text-base niuu-text-text-primary niuu-font-semibold">
                        {mount.name}
                      </span>
                      <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                        {mount.kind} · {mount.role}
                      </span>
                    </div>
                    <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted niuu-mt-1">
                      {mount.url || mount.path || 'no endpoint configured'}
                    </div>
                    <p className="niuu-m-0 niuu-mt-2 niuu-text-sm niuu-text-text-secondary">
                      {mount.desc || 'No description provided.'}
                    </p>
                  </div>
                  <div className="niuu-flex niuu-gap-2">
                    <button
                      type="button"
                      className="niuu-py-1.5 niuu-px-3 niuu-bg-bg-secondary niuu-border niuu-border-solid niuu-border-border niuu-rounded-md niuu-text-text-primary niuu-font-sans niuu-text-xs niuu-cursor-pointer"
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
                    <button
                      type="button"
                      className="niuu-py-1.5 niuu-px-3 niuu-bg-transparent niuu-border niuu-border-solid niuu-border-border-subtle niuu-rounded-md niuu-text-critical niuu-font-sans niuu-text-xs niuu-cursor-pointer"
                      onClick={() => deleteMount.mutate(mount.id)}
                      disabled={deleteMount.isPending}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                <div className="niuu-flex niuu-flex-wrap niuu-gap-2 niuu-mt-3 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                  <span>health: {mount.healthStatus}</span>
                  <span>priority: {mount.defaultReadPriority}</span>
                  <span>{mount.enabled ? 'enabled' : 'disabled'}</span>
                  {(mount.categories ?? []).length > 0 && (
                    <span>categories: {mount.categories?.join(', ')}</span>
                  )}
                </div>

                {isEditing && (
                  <div className="niuu-mt-4">
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

          {registryMounts.length === 0 && (
            <div className="niuu-p-6 niuu-border niuu-border-dashed niuu-border-border-subtle niuu-rounded-lg niuu-text-sm niuu-text-text-muted">
              No registered Mimir instances yet.
            </div>
          )}
        </section>

        <RegistryMountEditor
          heading="Add registry mount"
          mount={draft}
          submitLabel="Create"
          isPending={createMount.isPending}
          onChange={setDraft}
          onSubmit={() =>
            createMount.mutate(draft, {
              onSuccess: () => setDraft(EMPTY_REGISTRY_MOUNT),
            })
          }
          onReset={() => setDraft(EMPTY_REGISTRY_MOUNT)}
        />
      </div>
    </div>
  );
}
