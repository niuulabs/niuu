import { useParams, useSearch } from '@tanstack/react-router';
import { useUiMode } from '@niuulabs/shell';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import { SessionsPage } from './SessionsPage';
import { SimpleSessionsPage } from './SimpleSessionsPage';

/** Sessions: the calm list in Simple mode, the full forge console in Advanced. */
export function VolundrSessionsRoute() {
  const mode = useUiMode();
  if (mode === 'simple') return <SimpleSessionsPage />;
  return <SessionsPage />;
}

export function VolundrSessionRoute() {
  const { sessionId } = useParams({ strict: false });
  const search = useSearch({ strict: false }) as { instance_id?: unknown; returnTo?: unknown };
  const instanceId =
    typeof search.instance_id === 'string' && search.instance_id.trim()
      ? search.instance_id
      : undefined;
  return (
    <LiveSessionDetailPage
      sessionId={sessionId as string}
      instanceId={instanceId}
      returnTo={typeof search.returnTo === 'string' ? search.returnTo : undefined}
    />
  );
}

export function VolundrArchivedRoute() {
  const { sessionId } = useParams({ strict: false });
  const search = useSearch({ strict: false }) as { instance_id?: unknown; returnTo?: unknown };
  const instanceId =
    typeof search.instance_id === 'string' && search.instance_id.trim()
      ? search.instance_id
      : undefined;
  return (
    <LiveSessionDetailPage
      sessionId={sessionId as string}
      instanceId={instanceId}
      returnTo={typeof search.returnTo === 'string' ? search.returnTo : undefined}
      readOnly
    />
  );
}
