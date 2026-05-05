import { useRouterState, useRouter } from '@tanstack/react-router';

const SECTION_LABELS: Record<string, string> = {
  '/tyr/settings': 'Settings',
  '/tyr/settings/general': 'General',
  '/tyr/settings/dispatch': 'Dispatch rules',
  '/tyr/settings/integrations': 'Integrations',
  '/tyr/settings/personas': 'Persona overrides',
  '/tyr/settings/gates': 'Gates & reviewers',
  '/tyr/settings/flock': 'Flock Config',
  '/tyr/settings/notifications': 'Notifications',
  '/tyr/settings/advanced': 'Advanced',
  '/tyr/settings/audit': 'Audit Log',
};

export function SettingsTopbar() {
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const router = useRouter();
  const pathname = location.pathname;

  const isOnSettings = pathname === '/tyr/settings' || pathname.startsWith('/tyr/settings/');
  if (!isOnSettings) return null;

  const sectionLabel = SECTION_LABELS[pathname] ?? 'Settings';

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-3">
      <button
        type="button"
        onClick={() => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          void router.navigate({ to: '/tyr' as any });
        }}
        className="niuu-text-sm niuu-text-text-secondary hover:niuu-text-text-primary niuu-transition-colors"
        aria-label="Back to Tyr"
      >
        ← Tyr
      </button>
      <span className="niuu-text-text-muted">/</span>
      <span className="niuu-text-sm niuu-text-text-primary niuu-font-medium">{sectionLabel}</span>
    </div>
  );
}
