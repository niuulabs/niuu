import { Navigate, useParams } from '@tanstack/react-router';

/** Settings moved into the workspace's Resident and Connections tabs. */
export function RealmSettingsPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  return (
    <Navigate to="/realms/$slug" params={{ slug }} search={{ tab: 'resident' } as never} replace />
  );
}
