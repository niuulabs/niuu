import { useRouterState, useRouter } from '@tanstack/react-router';

const SECTION_LABELS: Record<string, string> = {
  '/ting/settings': 'Settings',
  '/ting/settings/general': 'General',
  '/ting/settings/dispatch': 'Dispatch rules',
  '/ting/settings/integrations': 'Integrations',
  '/ting/settings/personas': 'Persona overrides',
  '/ting/settings/gates': 'Gates & reviewers',
  '/ting/settings/flock': 'Flock Config',
  '/ting/settings/notifications': 'Notifications',
  '/ting/settings/advanced': 'Advanced',
  '/ting/settings/audit': 'Audit Log',
};

export function SettingsTopbar() {
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const router = useRouter();
  const pathname = location.pathname;

  const isOnSettings = pathname === '/ting/settings' || pathname.startsWith('/ting/settings/');
  if (!isOnSettings) return null;

  const sectionLabel = SECTION_LABELS[pathname] ?? 'Settings';

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-3">
      <button
        type="button"
        onClick={() => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          void router.navigate({ to: '/ting' as any });
        }}
        className="niuu-text-sm niuu-text-text-secondary hover:niuu-text-text-primary niuu-transition-colors"
        aria-label="Back to Ting"
      >
        ← Ting
      </button>
      <span className="niuu-text-text-muted">/</span>
      <span className="niuu-text-sm niuu-text-text-primary niuu-font-medium">{sectionLabel}</span>
    </div>
  );
}
