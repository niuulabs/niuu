import type { ReactNode } from 'react';
import type { AnyRoute } from '@tanstack/react-router';

export interface PluginCtx {
  tweaks: Record<string, unknown>;
  setTweak: (key: string, value: unknown) => void;
}

export interface PluginTab {
  id: string;
  label: string;
  rune?: string;
  /**
   * Route path for this tab. Defaults to `/${pluginId}/${id}`.
   * Use this to map a tab to the plugin root (e.g. `path: '/ting'` for dashboard).
   */
  path?: string;
  /** Optional count badge rendered next to the tab label. */
  count?: number;
  /**
   * Show this tab only while Simple mode is on. Advanced mode hides it, so a
   * plugin can offer a simplified page under its own id without changing the
   * tabs Advanced users know.
   */
  simpleOnly?: boolean;
}

/** How a plugin appears while Simple mode is on. */
export interface PluginSimpleMode {
  /** Tab ids to keep; all tabs when omitted. */
  tabs?: string[];
  /** Replaces the rune in the rail and topbar: a picture of what the plugin does. */
  icon?: ReactNode;
  /** Replaces `title` in the rail tooltip and topbar (e.g. "Sessions" for Völundr). */
  title?: string;
  /** Replaces `subtitle` in the rail tooltip and topbar. */
  subtitle?: string;
  /** Show this plugin only in Simple mode; Advanced hides it from the rail. */
  only?: boolean;
  /** The index route (`/`) lands here while Simple mode is on. */
  landing?: boolean;
}

export interface PluginDescriptor {
  id: string;
  rune: string;
  title: string;
  subtitle: string;
  /**
   * System plugins register routes but are excluded from the navigation rail
   * and from the default index redirect. Use for cross-cutting routes like
   * /login and /login/callback.
   */
  system?: boolean;

  /**
   * Where to place this plugin in the navigation rail.
   * `'bottom'` renders it below the spacer, pinned to the rail footer.
   * Defaults to `'top'`.
   */
  position?: 'top' | 'bottom';

  /**
   * How this plugin appears in Simple mode. A plugin that declares `simple`
   * stays in the rail with only the listed tab ids (all tabs when omitted);
   * a plugin without it is hidden from the rail while Simple mode is on.
   * Routes are unaffected: deep links keep working in either mode.
   * `icon` replaces the rune in the rail and topbar while Simple mode is on:
   * a picture of what the plugin does, for people who do not read runes yet.
   */
  simple?: PluginSimpleMode;

  routes?: (rootRoute: AnyRoute) => AnyRoute[];

  render?: (ctx: PluginCtx) => ReactNode;
  subnav?: (ctx: PluginCtx) => ReactNode;
  topbarRight?: (ctx: PluginCtx) => ReactNode;

  /** Tabs rendered in the topbar next to the plugin title. */
  tabs?: PluginTab[];
  /** Currently active tab id. */
  activeTab?: string;
  /** Callback when a tab is selected. */
  onTab?: (tabId: string) => void;
  /** Status chips rendered in the shell footer when this plugin is active. */
  footer?: (ctx: PluginCtx) => ReactNode;
}

export function definePlugin(descriptor: PluginDescriptor): PluginDescriptor {
  return descriptor;
}
