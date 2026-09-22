import { createElement, useCallback, useEffect, useMemo, type ReactNode } from 'react';
import clsx from 'clsx';
import { Outlet, useRouter, useRouterState } from '@tanstack/react-router';
import { type PluginCtx, type PluginDescriptor } from '@niuulabs/plugin-sdk';
import {
  Kbd,
  Tooltip,
  TooltipProvider,
  useCommandPalette,
  useCommandPaletteRegistry,
} from '@niuulabs/ui';
import { useTheme, type ThemeName } from '@niuulabs/design-tokens';
import { useShellContext } from './ShellContext';
import {
  isVisibleInMode,
  pluginFace,
  tabsForMode,
  useUiMode,
  useUiModePreferenceSync,
} from './uiMode';
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
  const { enabled, brand, version, ctx, topbarContent } = useShellContext();
  const router = useRouter();
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const pathname = location.pathname;
  const { setOpen } = useCommandPalette();
  const { register, unregister } = useCommandPaletteRegistry();
  const modePreferenceError = useUiModePreferenceSync();

  // System plugins (e.g. login) register routes but stay out of the nav rail.
  const allNavPlugins = useMemo(() => enabled.filter((p) => !p.system), [enabled]);
  const storedMode = useUiMode();
  // Simple mode only exists when at least one plugin opted into it; a host whose
  // plugins declare nothing gets the whole shell and no switch.
  const simpleAvailable = useMemo(() => allNavPlugins.some((p) => p.simple), [allNavPlugins]);
  const mode = simpleAvailable ? storedMode : 'advanced';
  const activeId = activePluginId(pathname, allNavPlugins);
  // Simple mode hides plugins from the rail, never from the router: a plugin reached by
  // deep link keeps its rail item while it is the active one.
  const navPlugins = useMemo(
    () => allNavPlugins.filter((p) => isVisibleInMode(p, mode) || p.id === activeId),
    [activeId, allNavPlugins, mode],
  );
  const topPlugins = useMemo(() => navPlugins.filter((p) => p.position !== 'bottom'), [navPlugins]);
  const bottomPlugins = useMemo(
    () => navPlugins.filter((p) => p.position === 'bottom'),
    [navPlugins],
  );

  const active = navPlugins.find((p) => p.id === activeId) ?? navPlugins[0] ?? null;
  const activeTabs = active ? tabsForMode(active, mode) : undefined;
  const face = (plugin: PluginDescriptor) => pluginFace(plugin, mode);
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
      const label = pluginFace(plugin, mode);
      register({
        id: `switch:${plugin.id}`,
        title: label.title,
        subtitle: label.subtitle,
        keywords: ['switch', 'navigate', 'go', 'plugin', plugin.id],
        execute: () => handleSelect(plugin.id),
      });
    }
    return () => {
      for (const plugin of navPlugins) {
        unregister(`switch:${plugin.id}`);
      }
    };
  }, [navPlugins, mode, register, unregister, handleSelect]);

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
              content={<RailTooltipContent title={face(p).title} subtitle={face(p).subtitle} />}
            >
              <button
                type="button"
                className={clsx(
                  'niuu-shell__rail-item',
                  p.icon && 'niuu-shell__rail-item--icon',
                  active?.id === p.id && 'niuu-shell__rail-item--active',
                )}
                title={[face(p).title, face(p).subtitle].filter(Boolean).join(' · ')}
                aria-label={face(p).title}
                data-testid={`rail-item-${p.id}`}
                onClick={() => handleSelect(p.id)}
              >
                {face(p).glyph}
              </button>
            </Tooltip>
          ))}
          <div className="niuu-shell__rail-spacer" />
          {bottomPlugins.map((p) => (
            <Tooltip
              key={p.id}
              side="right"
              delayMs={0}
              content={<RailTooltipContent title={face(p).title} subtitle={face(p).subtitle} />}
            >
              <button
                type="button"
                className={clsx(
                  'niuu-shell__rail-item',
                  p.icon && 'niuu-shell__rail-item--icon',
                  active?.id === p.id && 'niuu-shell__rail-item--active',
                )}
                title={[face(p).title, face(p).subtitle].filter(Boolean).join(' · ')}
                aria-label={face(p).title}
                data-testid={`rail-item-${p.id}`}
                onClick={() => handleSelect(p.id)}
              >
                {face(p).glyph}
              </button>
            </Tooltip>
          ))}
          <div className="niuu-shell__rail-foot">v{version}</div>
        </aside>

        <header className="niuu-shell__topbar">
          <div className="niuu-shell__topbar-title">
            {active && (
              <>
                <span className="niuu-shell__rune-mark">{face(active).glyph}</span>
                <h1>{face(active).title}</h1>
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
                    {t.count != null && t.count > 0 && (
                      <span className="niuu-shell__tab-count" data-testid={`tab-count-${t.id}`}>
                        {t.count}
                      </span>
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
            <select
              className="niuu-shell__theme-select"
              aria-label="Color theme"
              value={theme}
              onChange={(event) => setTheme(event.target.value as ThemeName)}
            >
              <option value="xteo">blue</option>
              <option value="ice">Native dark</option>
              <option value="amber">Amber</option>
              <option value="spring">Spring</option>
            </select>
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
            {active && <code>plugin:{active.id}</code>}
            <span className="niuu-shell__footer-sep">·</span>
            <span>niuu.world</span>
          </div>
          <div className="niuu-shell__footer-center" data-testid="footer-status">
            <PluginSlot render={active?.footer ?? null} ctx={ctx} />
          </div>
          <div className="niuu-shell__footer-right">
            {modePreferenceError ? (
              <span className="niuu-shell__mode-error" role="alert" title={modePreferenceError}>
                interface preference unavailable
              </span>
            ) : null}
            <span>{enabled.length} plugins loaded</span>
          </div>
        </footer>
      </div>
    </TooltipProvider>
  );
}
