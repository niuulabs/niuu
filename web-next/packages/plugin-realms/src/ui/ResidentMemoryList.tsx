import { Link } from '@tanstack/react-router';
import { EmptyState } from '@niuulabs/ui';
import type { ResidentMemoryEntry } from '../domain/join';

const BUTTON =
  'niuu:shrink-0 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary';

function storeStatus({ store, mount }: ResidentMemoryEntry): string {
  if (mount) return `${mount.pages} pages · ${mount.status}`;
  if (store.platformMount) return `mount ${store.platformMount} is not discovered on this platform`;
  return 'kept by the resident; the platform does not serve it';
}

/**
 * The memory a running resident reports, for a workspace made around it. Each store
 * opens in Memory by the platform mount the resident names; one it does not serve
 * is said to be unserved rather than hidden.
 */
export function ResidentMemoryList({
  residentName,
  entries,
}: {
  residentName: string;
  entries: ResidentMemoryEntry[];
}) {
  if (entries.length === 0) {
    return (
      <EmptyState
        title="No workspace memory"
        description={`${residentName} has not reported where it keeps its memory. A workspace made around a running resident does not create another.`}
      />
    );
  }
  return (
    <div className="niuu:flex niuu:flex-col" data-testid="resident-memory">
      {entries.map((entry) => (
        <div
          key={entry.store.name}
          className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2.5 niuu:text-xs niuu:last:border-b-0"
          data-testid={`resident-memory-${entry.store.name}`}
        >
          <span className="niuu:font-mono niuu:text-text-primary">{entry.store.name}</span>
          <span className="niuu:text-text-muted">{entry.store.role}</span>
          <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:text-text-secondary">
            {storeStatus(entry)}
          </span>
          {entry.mount ? (
            <Link to="/mimir" search={{ mount: entry.mount.name }} className={BUTTON}>
              Explore in Memory
            </Link>
          ) : null}
        </div>
      ))}
    </div>
  );
}
