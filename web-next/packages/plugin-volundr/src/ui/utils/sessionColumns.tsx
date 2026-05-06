import { LifecycleBadge } from '@niuulabs/ui';
import type { TableColumn } from '@niuulabs/ui';
import { toLifecycleState } from './toLifecycleState';
import type { Session } from '../../domain/session';

type ColumnKey = 'id' | 'persona' | 'cluster' | 'state' | 'started' | 'actions';

interface BuildSessionColumnsOptions {
  onView: (id: string) => void;
  buttonLabel?: string;
  testIdPrefix?: string;
  columns?: ColumnKey[];
}

/**
 * Builds a column set for session Table components.
 * Pass `columns` to select and order which columns to include.
 */
export function buildSessionColumns({
  onView,
  buttonLabel = 'View →',
  testIdPrefix = 'view-session',
  columns = ['id', 'persona', 'cluster', 'state', 'started', 'actions'],
}: BuildSessionColumnsOptions): TableColumn<Session>[] {
  const actionLabel = buttonLabel.replace(/\s*→$/, '');
  const all: Record<ColumnKey, TableColumn<Session>> = {
    id: {
      key: 'id',
      header: 'Session',
      render: (s) => (
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary">{s.id}</span>
      ),
    },
    persona: {
      key: 'persona',
      header: 'Persona',
      render: (s) => <span className="niuu-text-sm niuu-text-text-secondary">{s.personaName}</span>,
    },
    cluster: {
      key: 'cluster',
      header: 'Cluster',
      render: (s) => (
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">{s.clusterId}</span>
      ),
    },
    state: {
      key: 'state',
      header: 'State',
      render: (s) => <LifecycleBadge state={toLifecycleState(s.state)} />,
    },
    started: {
      key: 'started',
      header: 'Started',
      render: (s) => (
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">
          {new Date(s.startedAt).toLocaleString()}
        </span>
      ),
    },
    actions: {
      key: 'actions',
      header: '',
      render: (s) => (
        <button
          className="niuu-rounded niuu-px-2 niuu-py-1 niuu-text-xs niuu-text-brand hover:niuu-bg-bg-elevated"
          onClick={() => onView(s.id)}
          data-testid={`${testIdPrefix}-${s.id}`}
          aria-label={`${actionLabel} session ${s.id}`}
        >
          {buttonLabel}
        </button>
      ),
    },
  };
  return columns.map((key) => all[key]);
}
