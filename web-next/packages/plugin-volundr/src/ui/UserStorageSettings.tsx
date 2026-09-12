import { useState } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { Dialog, DialogContent, Table, LoadingState, ErrorState, EmptyState } from '@niuulabs/ui';
import { useService } from '@niuulabs/plugin-sdk';
import './UserStorageSettings.css';
import type { IVolundrService } from '../ports/IVolundrService';

const STORAGE_PREPARATION_POLL_MS = 2000;
const errorText = (error: Error & { detail?: string }) => error.detail ?? error.message;
const bytes = (value: number) => `${(value / 1024 ** 3).toFixed(1)} GiB`;

export function UserStorageSettings() {
  const service = useService<IVolundrService>('volundr');
  const [selected, setSelected] = useState('');
  const targets = useQuery({
    queryKey: ['volundr', 'storage-targets'],
    queryFn: () => service.getTargets(),
  });
  const clusterId = selected || targets.data?.[0]?.id || '';
  return (
    <div className="niuu-user-storage">
      <label className="niuu-user-storage__cluster">
        <span>Cluster</span>
        <select
          aria-label="Storage cluster"
          value={clusterId}
          onChange={(e) => setSelected(e.target.value)}
          className="niuu-form-control"
        >
          {targets.data?.map((target) => (
            <option key={target.id} value={target.id}>
              {target.name}
            </option>
          ))}
        </select>
      </label>
      <p className="niuu-user-storage__hint">
        Your home, temporary files and caches are private to your account on each cluster. Stop
        sessions on the selected cluster before deleting files.
      </p>
      {targets.isPending && <LoadingState label="Loading clusters…" />}
      {targets.error && (
        <ErrorState title="Unable to load clusters" message={errorText(targets.error)} />
      )}
      {targets.data?.length === 0 && (
        <EmptyState title="No clusters are available to your account." />
      )}
      {clusterId && <HomeBrowser key={clusterId} clusterId={clusterId} service={service} />}
    </div>
  );
}

function HomeBrowser({ clusterId, service }: { clusterId: string; service: IVolundrService }) {
  const queryClient = useQueryClient();
  const [path, setPath] = useState('');
  const [deleting, setDeleting] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ['volundr', 'user-home', clusterId, path],
    queryFn: () => service.listUserHome(clusterId, path),
    retry: false,
    refetchInterval: (q) =>
      q.state.data?.status === 'starting' ? STORAGE_PREPARATION_POLL_MS : false,
  });
  const remove = useMutation({
    mutationFn: (entryPath: string) => service.deleteUserHomePath(clusterId, entryPath),
    onSuccess: async () => {
      setDeleting(null);
      await queryClient.invalidateQueries({ queryKey: ['volundr', 'user-home', clusterId] });
    },
  });
  const listing = query.data;
  const buttonClass = 'niuu-storage-action';
  return (
    <section aria-label="Your home files" className="niuu-storage-browser">
      <div className="niuu-storage-browser__toolbar">
        <div className="niuu-storage-browser__locations" aria-label="Storage locations">
          {[
            { label: 'Home', path: '' },
            { label: 'Temporary files', path: 'tmp/sessions' },
            { label: 'Caches', path: 'tmp/cache' },
          ].map((location) => (
            <button
              key={location.path}
              className={buttonClass}
              aria-pressed={
                location.path
                  ? path === location.path || path.startsWith(`${location.path}/`)
                  : !path.startsWith('tmp/')
              }
              onClick={() => setPath(location.path)}
            >
              {location.label}
            </button>
          ))}
        </div>
        <button className={buttonClass} onClick={() => void query.refetch()}>
          Refresh
        </button>
      </div>
      <div className="niuu-storage-browser__path">
        <button
          className={buttonClass}
          disabled={!path}
          onClick={() => setPath(path.split('/').slice(0, -1).join('/'))}
        >
          Up
        </button>
        <code>{path ? `Home/${path}` : 'Home'}</code>
      </div>
      {listing?.capacity_bytes !== undefined && listing.available_bytes !== undefined && (
        <p className="niuu-storage-browser__capacity">
          {bytes(listing.available_bytes)} available of {bytes(listing.capacity_bytes)}
        </p>
      )}
      {(query.isPending || listing?.status === 'starting') && (
        <LoadingState label={listing?.detail ?? 'Opening your home storage…'} />
      )}
      {query.error && (
        <ErrorState title="Unable to open this folder" message={errorText(query.error)} />
      )}
      {listing?.status === 'ready' && listing.entries?.length ? (
        <Table
          aria-label="Home files"
          rows={listing.entries.map((entry) => ({ ...entry, id: entry.path }))}
          columns={[
            {
              key: 'name',
              header: 'Name',
              render: (entry) => (
                <span className="niuu-storage-browser__entry">
                  <span aria-hidden="true" className="niuu-storage-browser__icon">
                    {entry.kind === 'directory' ? '▸' : '·'}
                  </span>
                  {entry.kind === 'directory' ? (
                    <button
                      className="niuu-storage-browser__folder"
                      onClick={() => setPath(entry.path)}
                    >
                      {entry.name}
                    </button>
                  ) : (
                    <span>{entry.name}</span>
                  )}
                </span>
              ),
            },
            {
              key: 'kind',
              header: 'Type',
              render: (entry) => (
                <span className="niuu-storage-browser__metadata">{entry.kind}</span>
              ),
            },
            {
              key: 'size',
              header: 'Size',
              render: (entry) => (
                <span className="niuu-storage-browser__metadata">
                  {entry.kind === 'file' ? `${entry.size.toLocaleString()} B` : '—'}
                </span>
              ),
            },
            {
              key: 'actions',
              header: <span className="niuu:sr-only">Actions</span>,
              render: (entry) => (
                <button
                  className="niuu-storage-action niuu-storage-action--delete"
                  aria-label={`Delete ${entry.name}`}
                  onClick={() => {
                    remove.reset();
                    setDeleting(entry.path);
                  }}
                >
                  Delete
                </button>
              ),
            },
          ]}
        />
      ) : listing?.status === 'ready' ? (
        <EmptyState title="This folder is empty." />
      ) : null}
      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleting(null);
        }}
      >
        <DialogContent title="Delete home files">
          <p className="niuu-user-storage__hint">
            Permanently delete <code>Home/{deleting}</code> and its contents on this cluster? This
            cannot be undone.
          </p>
          {remove.error && <p role="alert">{errorText(remove.error)}</p>}
          <div className="niuu-storage-browser__dialog-actions">
            <button
              className={buttonClass}
              disabled={remove.isPending}
              onClick={() => setDeleting(null)}
            >
              Cancel
            </button>{' '}
            <button
              className="niuu-storage-action niuu-storage-action--delete"
              disabled={remove.isPending}
              onClick={() => remove.mutate(deleting!)}
            >
              Delete permanently
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
