import { Navigate, useParams } from '@tanstack/react-router';

/** Settings live in the workspace's Settings and Connections tabs. */
export function RealmSettingsPage() {
  const { slug } = useParams({ strict: false }) as { slug: string };
  return (
    <Navigate to="/realms/$slug" params={{ slug }} search={{ tab: 'settings' } as never} replace />
  );
}
