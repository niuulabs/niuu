import { useState } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { Dialog, DialogContent } from '@niuulabs/ui';
import { useService } from '@niuulabs/plugin-sdk';
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
    <div className="niuu:space-y-4">
      <label className="niuu:block niuu:space-y-2">
        <span>Cluster</span>
        <select
          aria-label="Storage cluster"
          value={clusterId}
          onChange={(e) => setSelected(e.target.value)}
          className="niuu:block niuu:rounded niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-2 niuu:text-text-primary"
        >
          {targets.data?.map((target) => (
            <option key={target.id} value={target.id}>
              {target.name}
            </option>
          ))}
        </select>
      </label>
      <p>
        Your home, temporary files and caches are private to your account on each cluster. Stop
        sessions on the selected cluster before deleting files.
      </p>
      {targets.isPending && <p role="status">Loading clusters…</p>}
      {targets.error && <p role="alert">{errorText(targets.error)}</p>}
      {targets.data?.length === 0 && <p>No clusters are available to your account.</p>}
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
  const buttonClass =
    'niuu:rounded niuu:border niuu:border-border-subtle niuu:px-3 niuu:py-1 niuu:disabled:opacity-50';
  return (
    <section aria-label="Your home files" className="niuu:space-y-3">
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <button className={buttonClass} onClick={() => setPath('')}>
          Home
        </button>
        <button className={buttonClass} onClick={() => setPath('tmp/sessions')}>
          Temporary files
        </button>
        <button className={buttonClass} onClick={() => setPath('tmp/cache')}>
          Caches
        </button>
        <button className={buttonClass} onClick={() => void query.refetch()}>
          Refresh
        </button>
      </div>
      <div className="niuu:flex niuu:items-center niuu:gap-3">
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
        <p>
          {bytes(listing.available_bytes)} available of {bytes(listing.capacity_bytes)}
        </p>
      )}
      {(query.isPending || listing?.status === 'starting') && (
        <p role="status">{listing?.detail ?? 'Opening your home storage…'}</p>
      )}
      {query.error && <p role="alert">{errorText(query.error)}</p>}
      {listing?.status === 'ready' && (
        <table className="niuu:w-full niuu:text-left">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Size</th>
              <th>
                <span className="niuu:sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {listing.entries?.map((entry) => (
              <tr key={entry.path}>
                <td>
                  {entry.kind === 'directory' ? (
                    <button
                      className="niuu:py-2 niuu:text-brand"
                      onClick={() => setPath(entry.path)}
                    >
                      {entry.name}
                    </button>
                  ) : (
                    entry.name
                  )}
                </td>
                <td>{entry.kind}</td>
                <td>{entry.kind === 'file' ? `${entry.size.toLocaleString()} B` : '—'}</td>
                <td>
                  <button
                    className={buttonClass}
                    aria-label={`Delete ${entry.name}`}
                    onClick={() => {
                      remove.reset();
                      setDeleting(entry.path);
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {listing?.status === 'ready' && listing.entries?.length === 0 && <p>This folder is empty.</p>}
      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleting(null);
        }}
      >
        <DialogContent title="Delete home files">
          <p>
            Permanently delete <code>Home/{deleting}</code> and its contents on this cluster? This
            cannot be undone.
          </p>
          {remove.error && <p role="alert">{errorText(remove.error)}</p>}
          <button
            className={buttonClass}
            disabled={remove.isPending}
            onClick={() => setDeleting(null)}
          >
            Cancel
          </button>{' '}
          <button
            className={buttonClass}
            disabled={remove.isPending}
            onClick={() => remove.mutate(deleting!)}
          >
            Delete permanently
          </button>
        </DialogContent>
      </Dialog>
    </section>
  );
}
