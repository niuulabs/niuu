import { flushSync } from 'react-dom';
import { createElement, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react';
import clsx from 'clsx';
import { Outlet, useRouter, useRouterState } from '@tanstack/react-router';
import { type PluginCtx, type PluginDescriptor, type PluginTab } from '@niuulabs/plugin-sdk';
import {
  Kbd,
  Popover,
  PopoverTrigger,
  PopoverContent,
  PopoverClose,
  Tooltip,
  TooltipProvider,
  useCommandPalette,
  useCommandPaletteRegistry,
} from '@niuulabs/ui';
import { useTheme, type ThemeName } from '@niuulabs/design-tokens';
import { useShellContext } from './ShellContext';
import './Shell.css';

function pathMatches(pathname: string, basePath: string): boolean {
  return pathname === basePath || pathname.startsWith(basePath + '/');
}

function activePluginId(pathname: string, plugins: PluginDescriptor[]): string | null {
  for (const plugin of plugins) {
    if (pathMatches(pathname, `/${plugin.id}`)) return plugin.id;
    if (plugin.tabs?.some((tab) => pathMatches(pathname, tab.path ?? `/${plugin.id}/${tab.id}`))) {
      return plugin.id;
    }
  }
  return null;
}

export function PluginSlot({
  render,
  ctx,
}: {
  render?: ((ctx: PluginCtx) => ReactNode) | null;
  ctx: PluginCtx;
}) {
  return render ? createElement(render, ctx) : null;
}

function TabCountBadge({ tabId, count }: { tabId: string; count: number | null | undefined }) {
  if (count == null || count <= 0) return null;
  return (
    <span className="niuu-shell__tab-count" data-testid={`tab-count-${tabId}`}>
      {count}
    </span>
  );
}

function LiveTabCount({
  tabId,
  useCount,
}: {
  tabId: string;
  useCount: NonNullable<PluginTab['useCount']>;
}) {
  return <TabCountBadge tabId={tabId} count={useCount()} />;
}

function RailTooltipContent({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="niuu-shell__rail-tooltip">
      <strong>{title}</strong>
      {subtitle ? <span>{subtitle}</span> : null}
    </div>
  );
}

export function ShellLayout() {
  const { theme, setTheme } = useTheme();
  const themeButton = useRef<HTMLButtonElement>(null);

  function changeTheme(nextTheme: ThemeName) {
    if (nextTheme === theme) return;
    if (
      !document.startViewTransition ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      setTheme(nextTheme);
      return;
    }
    // Keep rapid clicks from overlapping snapshot animations.
    if (document.documentElement.classList.contains('theme-transitioning')) return;
    document.documentElement.classList.add('theme-transitioning');
    const transition = document.startViewTransition(() => {
      flushSync(() => setTheme(nextTheme));
    });
    const cleanup = () => document.documentElement.classList.remove('theme-transitioning');
    // A skipped/interrupted view transition still applies the theme update.
    void transition.finished.then(cleanup, cleanup);
  }

  const { enabled, brand, version, ctx, topbarContent } = useShellContext();
  const router = useRouter();
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const pathname = location.pathname;
  const { setOpen } = useCommandPalette();
  const { register, unregister } = useCommandPaletteRegistry();

  // System plugins (e.g. login) register routes but stay out of the nav rail.
  const navPlugins = useMemo(() => enabled.filter((p) => !p.system), [enabled]);
  const activeId = activePluginId(pathname, navPlugins);
  const topPlugins = useMemo(() => navPlugins.filter((p) => p.position !== 'bottom'), [navPlugins]);
  const bottomPlugins = useMemo(
    () => navPlugins.filter((p) => p.position === 'bottom'),
    [navPlugins],
  );

  const active = navPlugins.find((p) => p.id === activeId) ?? navPlugins[0] ?? null;
  const activeTabs = active?.tabs;
  const subnavCollapsed = active ? Boolean(ctx.tweaks[`${active.id}.subnavCollapsed`]) : false;

  // localStorage follows the router — not the other way around
  useEffect(() => {
    if (activeId && typeof window !== 'undefined') {
      localStorage.setItem('niuu.active', activeId);
    }
  }, [activeId]);

  const handleSelect = useCallback(
    (id: string) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      router.navigate({ to: `/${id}` as any });
    },
    [router],
  );

  // Register "switch plugin" default commands for all nav plugins
  useEffect(() => {
    for (const plugin of navPlugins) {
      register({
        id: `switch:${plugin.id}`,
        title: plugin.title,
        subtitle: plugin.subtitle,
        keywords: ['switch', 'navigate', 'go', 'plugin', plugin.id],
        execute: () => handleSelect(plugin.id),
      });
    }
    return () => {
      for (const plugin of navPlugins) {
        unregister(`switch:${plugin.id}`);
      }
    };
  }, [navPlugins, register, unregister, handleSelect]);

  return (
    <TooltipProvider>
      <div className="niuu-shell" data-theme={theme}>
        <aside className="niuu-shell__rail">
          <div className="niuu-shell__rail-brand" title="Niuu">
            {brand}
          </div>
          {topPlugins.map((p) => (
            <Tooltip
              key={p.id}
              side="right"
              delayMs={0}
              content={<RailTooltipContent title={p.title} subtitle={p.subtitle} />}
            >
              <button
                type="button"
                className={clsx(
                  'niuu-shell__rail-item',
                  p.icon && 'niuu-shell__rail-item--icon',
                  active?.id === p.id && 'niuu-shell__rail-item--active',
                )}
                title={[p.title, p.subtitle].filter(Boolean).join(' · ')}
                aria-label={p.title}
                data-testid={`rail-item-${p.id}`}
                onClick={() => handleSelect(p.id)}
              >
                {p.icon ?? p.rune}
              </button>
            </Tooltip>
          ))}
          <div className="niuu-shell__rail-spacer" />
          {bottomPlugins.map((p) => (
            <Tooltip
              key={p.id}
              side="right"
              delayMs={0}
              content={<RailTooltipContent title={p.title} subtitle={p.subtitle} />}
            >
              <button
                type="button"
                className={clsx(
                  'niuu-shell__rail-item',
                  p.icon && 'niuu-shell__rail-item--icon',
                  active?.id === p.id && 'niuu-shell__rail-item--active',
                )}
                title={[p.title, p.subtitle].filter(Boolean).join(' · ')}
                aria-label={p.title}
                data-testid={`rail-item-${p.id}`}
                onClick={() => handleSelect(p.id)}
              >
                {p.icon ?? p.rune}
              </button>
            </Tooltip>
          ))}
          <div className="niuu-shell__rail-foot">v{version}</div>
        </aside>

        <header className="niuu-shell__topbar">
          <div className="niuu-shell__topbar-title">
            {active && (
              <>
                <span className="niuu-shell__rune-mark">{active.icon ?? active.rune}</span>
                <h1>{active.title}</h1>
              </>
            )}
          </div>
          {active && activeTabs && (
            <div className="niuu-shell__tabs">
              {activeTabs.map((t) => {
                const tabPath = t.path ?? `/${active.id}/${t.id}`;
                const isActive =
                  active.activeTab != null
                    ? active.activeTab === t.id
                    : tabPath === `/${active.id}`
                      ? pathname === tabPath
                      : pathMatches(pathname, tabPath) &&
                        !activeTabs.some((other) => {
                          const otherPath = other.path ?? `/${active.id}/${other.id}`;
                          return (
                            otherPath.length > tabPath.length && pathMatches(pathname, otherPath)
                          );
                        });
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={clsx('niuu-shell__tab', isActive && 'niuu-shell__tab--active')}
                    data-testid={`${active.id}-tab-${t.id}`}
                    onClick={() => {
                      // eslint-disable-next-line @typescript-eslint/no-explicit-any
                      router.navigate({ to: tabPath as any });
                      active.onTab?.(t.id);
                    }}
                  >
                    {t.rune && <span className="niuu-shell__tab-rune">{t.rune}</span>}
                    <span className="niuu-shell__tab-label">{t.label}</span>
                    {t.useCount ? (
                      <LiveTabCount
                        key={`${active.id}:${t.id}`}
                        tabId={t.id}
                        useCount={t.useCount}
                      />
                    ) : (
                      <TabCountBadge tabId={t.id} count={t.count} />
                    )}
                  </button>
                );
              })}
            </div>
          )}
          <div className="niuu-shell__topbar-right">
            <div className="niuu-shell__plugin-status">
              <PluginSlot render={active?.topbarRight ?? null} ctx={ctx} />
            </div>
            <Popover>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  ref={themeButton}
                  className="niuu-shell__theme-picker"
                  aria-label="Color theme"
                  title="Color theme"
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    {theme === 'light' ? (
                      <>
                        <circle cx="12" cy="12" r="4" />
                        <path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" />
                      </>
                    ) : (
                      <path d="M20.9 13.2A9 9 0 0 1 10.8 3.1a9 9 0 1 0 10.1 10.1Z" />
                    )}
                  </svg>
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" className="niuu-shell__theme-options">
                {(
                  [
                    ['niuu', 'NIUU'],
                    ['xteo', 'blue'],
                    ['ice', 'Native dark'],
                    ['amber', 'Amber'],
                    ['spring', 'Spring'],
                    ['light', 'Light'],
                  ] as const
                ).map(([value, label]) => (
                  <PopoverClose asChild key={value}>
                    <button
                      type="button"
                      className="niuu-shell__theme-option"
                      aria-pressed={theme === value}
                      onClick={() => {
                        changeTheme(value);
                        requestAnimationFrame(() => themeButton.current?.focus());
                      }}
                    >
                      {label}
                    </button>
                  </PopoverClose>
                ))}
              </PopoverContent>
            </Popover>
            <button
              type="button"
              className="niuu-shell__cp-btn"
              onClick={() => setOpen(true)}
              aria-label="Open command palette"
            >
              <Kbd>⌘K</Kbd>
            </button>
            {topbarContent ? (
              <div className="niuu-shell__topbar-content">{topbarContent}</div>
            ) : null}
          </div>
        </header>

        <nav
          className={clsx('niuu-shell__subnav', subnavCollapsed && 'niuu-shell__subnav--collapsed')}
        >
          <PluginSlot render={active?.subnav ?? null} ctx={ctx} />
        </nav>

        <main className="niuu-shell__content">
          <Outlet />
        </main>

        <footer className="niuu-shell__footer">
          <div className="niuu-shell__footer-left">
            <span>niuu.world</span>
          </div>
          <div className="niuu-shell__footer-center" data-testid="footer-status">
            <PluginSlot render={active?.footer ?? null} ctx={ctx} />
          </div>
          <div className="niuu-shell__footer-right">
            <span>{enabled.length} plugins loaded</span>
          </div>
        </footer>
      </div>
    </TooltipProvider>
  );
}
