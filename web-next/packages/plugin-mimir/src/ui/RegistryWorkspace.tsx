import { usePluginCtx } from '@niuulabs/plugin-sdk';
import { useNavigate, useRouterState } from '@tanstack/react-router';
import { RegistryPage } from './RegistryPage';
import { HealthPage } from './HealthPage';
import { AnalyticsPage } from './AnalyticsPage';

const views = [
  { title: 'Instances', path: '/mimir/registry', component: RegistryPage },
  { title: 'Health', path: '/mimir/registry/health', component: HealthPage },
  { title: 'Analytics', path: '/mimir/registry/analytics', component: AnalyticsPage },
];
export function RegistryWorkspace() {
  const ctx = usePluginCtx();
  const path = useRouterState({ select: (state) => state.location.pathname });
  const navigate = useNavigate();
  const view =
    views.find((item) => item.title === ctx.tweaks['mimir.registryView']) ??
    views.find((item) => item.path === path) ??
    (/\/(health|lint|doctor)$/.test(path)
      ? views[1]
      : /\/(analytics|dreams|ravns|wardens)$/.test(path)
        ? views[2]
        : views[0])!;
  const Content = view.component;
  return (
    <div className="knowledge-workspace">
      <nav
        aria-label="Knowledge instance management"
        className="niuu:flex niuu:gap-2 niuu:px-6 niuu:py-3 niuu:border-b niuu:border-border-subtle"
      >
        {views.map((item) => (
          <button
            key={item.path}
            type="button"
            aria-current={view.path === item.path ? 'page' : undefined}
            className={`niuu:px-4 niuu:py-2 niuu:rounded-md niuu:text-sm niuu:cursor-pointer ${view.path === item.path ? 'niuu:bg-bg-tertiary niuu:text-text-primary' : 'niuu:bg-transparent niuu:text-text-muted'}`}
            onClick={() => {
              ctx.setTweak('mimir.registryView', '');
              void navigate({ to: item.path });
            }}
          >
            {item.title}
          </button>
        ))}
      </nav>
      <Content />
    </div>
  );
}
