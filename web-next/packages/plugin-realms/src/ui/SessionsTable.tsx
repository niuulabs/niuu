import { Link } from '@tanstack/react-router';
import type { VolundrSession } from '@niuulabs/plugin-volundr';
import { Chip, EmptyState, Table } from '@niuulabs/ui';
import { sessionRows, type SessionRow } from '../domain/sessionTree';

/** Static classes, so the prebuilt stylesheet carries them. Deeper rows share the last. */
const DEPTH_INDENT = ['', 'niuu:pl-5', 'niuu:pl-10', 'niuu:pl-15'] as const;

function indentFor(depth: number): string {
  return DEPTH_INDENT[Math.min(depth, DEPTH_INDENT.length - 1)] ?? '';
}

/** Sessions with what each one launched nested under it. */
export function SessionsTable({ sessions }: { sessions: VolundrSession[] }) {
  if (sessions.length === 0)
    return (
      <EmptyState
        title="No sessions yet"
        description="The resident starts one per ticket it picks up. You can launch one yourself above."
      />
    );
  return (
    <Table<SessionRow & { id: string }>
      columns={[
        {
          key: 'name',
          header: 'session',
          render: (row) => (
            <span
              className={`niuu:inline-block ${indentFor(row.depth)}`}
              data-testid={`session-depth-${row.depth}`}
            >
              <Link
                to={'/volundr/session/$sessionId' as never}
                params={{ sessionId: row.session.id } as never}
                className="niuu:text-text-primary"
              >
                {row.session.name}
              </Link>
            </span>
          ),
        },
        {
          key: 'role',
          header: 'role',
          render: (row) => (
            <span className="niuu:text-xs niuu:text-text-muted">
              {row.session.coordination?.role ?? ''}
            </span>
          ),
          width: '110px',
        },
        {
          key: 'status',
          header: 'state',
          render: (row) => (
            <Chip tone={row.session.status === 'running' ? 'brand' : 'muted'}>
              {row.session.status}
            </Chip>
          ),
          width: '140px',
        },
        {
          key: 'model',
          header: 'model',
          render: (row) => <span className="niuu:font-mono niuu:text-xs">{row.session.model}</span>,
          width: '160px',
        },
      ]}
      rows={sessionRows(sessions).map((row) => ({ ...row, id: row.session.id }))}
      aria-label="sessions"
    />
  );
}
