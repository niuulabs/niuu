import { useRouterState, useRouter } from '@tanstack/react-router';
import { cn } from '@niuulabs/ui';

interface NavItem {
  id: string;
  label: string;
  path: string;
}

const NAV_ITEMS: NavItem[] = [
  { id: 'general', label: 'General', path: '/tyr/settings/general' },
  { id: 'dispatch', label: 'Dispatch rules', path: '/tyr/settings/dispatch' },
  { id: 'integrations', label: 'Integrations', path: '/tyr/settings/integrations' },
  { id: 'personas', label: 'Persona overrides', path: '/tyr/settings/personas' },
  { id: 'gates', label: 'Gates & reviewers', path: '/tyr/settings/gates' },
  { id: 'flock', label: 'Flock Config', path: '/tyr/settings/flock' },
  { id: 'notifications', label: 'Notifications', path: '/tyr/settings/notifications' },
  { id: 'advanced', label: 'Advanced', path: '/tyr/settings/advanced' },
  { id: 'audit', label: 'Audit Log', path: '/tyr/settings/audit' },
];

export function SettingsRail() {
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const router = useRouter();
  const pathname = location.pathname;

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-0.5 niuu-py-2 niuu-px-1 niuu-min-w-[160px]">
      <p className="niuu-text-xs niuu-text-text-muted niuu-uppercase niuu-tracking-wide niuu-px-2 niuu-py-1">
        Settings
      </p>
      {NAV_ITEMS.map((item) => {
        const isActive = pathname === item.path || pathname.startsWith(item.path + '/');
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              void router.navigate({ to: item.path as any });
            }}
            className={cn(
              'niuu-w-full niuu-text-left niuu-px-3 niuu-py-1.5 niuu-rounded-md niuu-text-sm niuu-transition-colors',
              isActive
                ? 'niuu-bg-bg-elevated niuu-text-text-primary niuu-font-medium'
                : 'niuu-text-text-secondary hover:niuu-text-text-primary hover:niuu-bg-bg-secondary',
            )}
            aria-current={isActive ? 'page' : undefined}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
