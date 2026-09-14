import { useParams } from '@tanstack/react-router';
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
  return <LiveSessionDetailPage sessionId={sessionId as string} />;
}

export function VolundrArchivedRoute() {
  const { sessionId } = useParams({ strict: false });
  return <LiveSessionDetailPage sessionId={sessionId as string} readOnly />;
}
