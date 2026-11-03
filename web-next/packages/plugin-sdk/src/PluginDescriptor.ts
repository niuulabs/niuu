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
   * Optional live count badge. A React hook the shell calls inside its own badge
   * component, so it may read services or queries; it is only mounted while the
   * plugin's tabs are visible. Takes precedence over `count`. Return `null` or
   * `undefined` (or 0) to hide the badge.
   */
  useCount?: () => number | null | undefined;
}

export interface PluginDescriptor {
  id: string;
  rune: string;
  /** Optional full-size navigation icon, in place of the text rune. */
  icon?: ReactNode;
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
