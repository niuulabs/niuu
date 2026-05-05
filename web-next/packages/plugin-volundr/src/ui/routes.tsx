import { useParams } from '@tanstack/react-router';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';

export function VolundrSessionRoute() {
  const { sessionId } = useParams({ strict: false });
  return <LiveSessionDetailPage sessionId={sessionId as string} />;
}

export function VolundrArchivedRoute() {
  const { sessionId } = useParams({ strict: false });
  return <LiveSessionDetailPage sessionId={sessionId as string} readOnly />;
}
