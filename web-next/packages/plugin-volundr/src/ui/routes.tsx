import { useParams, useSearch } from '@tanstack/react-router';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import { SessionsPage } from './SessionsPage';
import { ComputeHostsPage } from './computeHosts/ComputeHostsPage';

/** `/volundr/hosts?instance_id=…`: the compute hosts of one Forge. */
export function VolundrHostsRoute() {
  const search = useSearch({ strict: false }) as { instance_id?: unknown };
  const instanceId =
    typeof search.instance_id === 'string' && search.instance_id.trim()
      ? search.instance_id
      : undefined;
  return <ComputeHostsPage instanceId={instanceId} />;
}

export function VolundrSessionsRoute() {
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
