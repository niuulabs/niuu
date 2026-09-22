import { describe, it, expect, afterEach, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryHistory, createRoute } from '@tanstack/react-router';
import {
  ConfigProvider,
  FeatureCatalogProvider,
  ServicesProvider,
  definePlugin,
  type IFeatureCatalogService,
} from '@niuulabs/plugin-sdk';
import { Shell } from './Shell';
import { PluginSlot } from './ShellLayout';
import { UI_MODE_STORAGE_KEY } from './uiMode';

// Plugins that use render() (no routes) — cover the render-fallback path.
const pluginA = definePlugin({
  id: 'alpha',
  rune: 'ᚨ',
  title: 'Alpha',
  subtitle: 'first',
  render: () => <div data-testid="alpha-content">alpha-rendered</div>,
});

const pluginB = definePlugin({
  id: 'beta',
  rune: 'ᛒ',
  title: 'Beta',
  subtitle: 'second',
  render: () => <div data-testid="beta-content">beta-rendered</div>,
});

const pluginWithSimpleMode = definePlugin({
  id: 'simple-capable',
  rune: 'S',
  title: 'Simple capable',
  subtitle: 'secondary description',
  simple: {},
  render: () => <div data-testid="simple-capable-content">simple-capable</div>,
});

// Plugin with tabs (including count badges), subnav, and footer
const pluginWithTabs = definePlugin({
  id: 'tabbed',
  rune: 'ᛐ',
  title: 'Tabbed',
  subtitle: 'tabs test',
  tabs: [
    { id: 'one', label: 'One', count: 4 },
    { id: 'two', label: 'Two', count: 0 },
    { id: 'three', label: 'Three' },
  ],
  render: () => <div data-testid="tabbed-content">tabbed-rendered</div>,
  subnav: () => <div data-testid="tabbed-subnav">subnav-content</div>,
  footer: () => <span data-testid="tabbed-footer-chip">api ● connected</span>,
});

const pluginWithCustomTabPath = definePlugin({
  id: 'valkyrie',
  rune: 'ᛒ',
  title: 'Valkyrie',
  subtitle: 'resident operators',
  tabs: [{ id: 'console', label: 'Console', path: '/valkyries' }],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/valkyries',
      component: () => <div data-testid="valkyrie-content">valkyrie-rendered</div>,
    }),
  ],
});

const pluginWithCollapsibleSubnav = definePlugin({
  id: 'collapsible',
  rune: 'ᚲ',
  title: 'Collapsible',
  subtitle: 'subnav test',
  render: () => <div data-testid="collapsible-content">collapsible-rendered</div>,
  subnav: (ctx) => (
    <button type="button" onClick={() => ctx.setTweak('collapsible.subnavCollapsed', true)}>
      collapse subnav
    </button>
  ),
});

// Plugin without subnav — tests collapse
const pluginNoSubnav = definePlugin({
  id: 'flat',
  rune: 'ᚠ',
  title: 'Flat',
  subtitle: 'no subnav',
  render: () => <div data-testid="flat-content">flat-rendered</div>,
});

// Top plugin with an icon and an empty subtitle — covers the icon rail-item class
// and the tooltip's no-subtitle branch.
const pluginTopIcon = definePlugin({
  id: 'topicon',
  rune: 'ᛏ'.replace('ᛏ', 'T'),
  icon: <span data-testid="topicon-icon">TI</span>,
  title: 'TopIcon',
  subtitle: '',
  render: () => <div data-testid="topicon-content">topicon-rendered</div>,
});

// Bottom-pinned plugin with an icon — covers the rail-footer section entirely.
const pluginBottomIcon = definePlugin({
  id: 'bikon',
  rune: 'B',
  icon: <span data-testid="bikon-icon">BI</span>,
  title: 'BottomIcon',
  subtitle: 'pinned below',
  position: 'bottom',
  render: () => <div data-testid="bikon-content">bikon-rendered</div>,
});

// Plugin whose tabs carry an explicit activeTab override and a rune glyph.
const pluginWithActiveTabOverride = definePlugin({
  id: 'override',
  rune: 'O',
  title: 'Override',
  subtitle: 'active tab override',
  activeTab: 'y',
  tabs: [
    { id: 'x', label: 'X' },
    { id: 'y', label: 'Y', rune: 'ᚱ' },
  ],
  render: () => <div data-testid="override-content">override-rendered</div>,
});

// Plugin with a tab whose path equals the plugin's own base route.
const pluginWithBaseTab = definePlugin({
  id: 'dash',
  rune: 'D',
  title: 'Dash',
  subtitle: 'base tab',
  tabs: [{ id: 'home', label: 'Home', path: '/dash' }],
  render: () => <div data-testid="dash-content">dash-rendered</div>,
});

// Plugin with two clickable tabs and an onTab callback — covers the tab onClick handler.
const pluginTabClick = definePlugin({
  id: 'tabclick',
  rune: 'C',
  title: 'TabClick',
  subtitle: 'click test',
  tabs: [
    { id: 'one', label: 'One' },
    { id: 'two', label: 'Two' },
  ],
  render: () => <div data-testid="tabclick-content">tabclick-rendered</div>,
});

// Plugin with two nested tab routes, neither of which equals the plugin's own base
// path — covers the "prefer the longer matching tab" exclusion logic (a shorter tab
// path that is a prefix of another, also-matching, longer tab path).
const pluginWithNestedTabs = definePlugin({
  id: 'nest',
  rune: 'N',
  title: 'Nest',
  subtitle: 'nested tabs',
  tabs: [
    { id: 'over', label: 'Over', path: '/nest/over', rune: 'ᚾ' },
    { id: 'under', label: 'Under', path: '/nest/over/under' },
  ],
  routes: (rootRoute) => [
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/nest/over',
      component: () => <div data-testid="nest-over-content">nest-over</div>,
    }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: '/nest/over/under',
      component: () => <div data-testid="nest-under-content">nest-under</div>,
    }),
  ],
});

function HookFooter() {
  const [status] = useState('connected');
  return <span data-testid="hook-footer">{status}</span>;
}

function DetailedHookFooter() {
  const [status] = useState('connected');
  const [detail] = useState('ready');
  return <span data-testid="hook-footer">{`${status} ${detail}`}</span>;
}

function wrap(
  ui: React.ReactNode,
  pluginOverrides: Record<string, { enabled: boolean; order: number }> = {},
) {
  return render(
    <ConfigProvider
      value={{
        demoMode: false,
        theme: 'ice',
        plugins: pluginOverrides,
        services: {},
      }}
    >
      <FeatureCatalogProvider>{ui}</FeatureCatalogProvider>
    </ConfigProvider>,
  );
}

/** A memory history pre-navigated to a path — keeps test runs isolated. */
function memHistory(path: string) {
  return createMemoryHistory({ initialEntries: [path] });
}

describe('Shell', () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it.each([false, true])(
    'keeps the current page after feature data arrives (reorder=%s)',
    async (reorder) => {
      const plugins = [pluginA, pluginB];
      const history = memHistory('/alpha');
      function Harness() {
        const [loaded, setLoaded] = useState(false);
        return (
          <ConfigProvider value={{ demoMode: false, theme: 'ice', plugins: {}, services: {} }}>
            <button onClick={() => setLoaded(true)}>Load features</button>
            <FeatureCatalogProvider
              overrides={{ order: (id) => (id === 'alpha' && loaded && reorder ? 2 : 1) }}
            >
              <Shell plugins={plugins} _testHistory={history} />
            </FeatureCatalogProvider>
          </ConfigProvider>
        );
      }
      render(<Harness />);
      const content = await screen.findByTestId('alpha-content');
      fireEvent.click(screen.getByRole('button', { name: 'Load features' }));
      await screen.findByTestId('alpha-content');
      expect(history.location.pathname).toBe('/alpha');
      if (!reorder) expect(screen.getByTestId('alpha-content')).toBe(content);
    },
  );

  it('renders the first enabled plugin by default', async () => {
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/')} />);
    // Index route redirects to /alpha (first enabled plugin)
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('beta-content')).not.toBeInTheDocument();
  });

  it('renders a plugin directly when memory history starts at its path', async () => {
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/beta')} />);
    await waitFor(() => {
      expect(screen.getByTestId('beta-content')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('alpha-content')).not.toBeInTheDocument();
  });

  it('keeps essential application controls directly available in the header', async () => {
    wrap(
      <Shell
        plugins={[pluginWithSimpleMode]}
        topbarContent={<button type="button">Disconnect</button>}
        _testHistory={memHistory('/simple-capable')}
      />,
    );
    await screen.findByTestId('simple-capable-content');

    expect(screen.queryByText('secondary description')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ui-mode-switch')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Color theme' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Open command palette' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Open application menu' })).not.toBeInTheDocument();
    expect(screen.queryByText('LIVE')).not.toBeInTheDocument();
  });

  it('switches active plugin on rail click and persists to localStorage', async () => {
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/')} />);
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Beta · second'));

    await waitFor(() => {
      expect(screen.getByTestId('beta-content')).toBeInTheDocument();
    });
    expect(localStorage.getItem('niuu.active')).toBe('beta');
  });

  it('shows a tooltip for rail items with plugin name and subtitle', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/')} />);
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });

    await user.hover(screen.getByRole('button', { name: 'Beta' }));

    await waitFor(() => {
      expect(screen.getByRole('tooltip')).toBeInTheDocument();
    });
    expect(screen.getByRole('tooltip')).toHaveTextContent('Beta');
    expect(screen.getByRole('tooltip')).toHaveTextContent('second');
  });

  it('hides plugins disabled via config', async () => {
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/')} />, {
      alpha: { enabled: false, order: 1 },
    });
    await waitFor(() => {
      expect(screen.queryByTitle('Alpha · first')).not.toBeInTheDocument();
      expect(screen.getByTestId('beta-content')).toBeInTheDocument();
    });
  });

  it('localStorage.niuu.active is written from the router state, not vice-versa', async () => {
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/beta')} />);
    await waitFor(() => {
      expect(screen.getByTestId('beta-content')).toBeInTheDocument();
    });
    // localStorage should have been updated from the route, not from a stored value
    expect(localStorage.getItem('niuu.active')).toBe('beta');
  });

  it('renders tab count badge when count > 0', async () => {
    wrap(<Shell plugins={[pluginWithTabs]} _testHistory={memHistory('/tabbed')} />);
    await waitFor(() => {
      expect(screen.getByTestId('tabbed-content')).toBeInTheDocument();
    });
    // Tab "One" has count=4 — badge should be visible
    const badge = screen.getByTestId('tab-count-one');
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toBe('4');
  });

  it('marks a plugin active when the route matches a custom tab path', async () => {
    wrap(
      <Shell
        plugins={[pluginA, pluginWithCustomTabPath]}
        _testHistory={memHistory('/valkyries')}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('valkyrie-content')).toBeInTheDocument();
    });

    expect(screen.getByRole('heading', { name: 'Valkyrie' })).toBeInTheDocument();
    expect(screen.getByTestId('valkyrie-tab-console')).toBeInTheDocument();
    expect(localStorage.getItem('niuu.active')).toBe('valkyrie');
  });

  it('does not render tab count badge when count is 0', async () => {
    wrap(<Shell plugins={[pluginWithTabs]} _testHistory={memHistory('/tabbed')} />);
    await waitFor(() => {
      expect(screen.getByTestId('tabbed-content')).toBeInTheDocument();
    });
    // Tab "Two" has count=0 — no badge
    expect(screen.queryByTestId('tab-count-two')).not.toBeInTheDocument();
  });

  it('does not render tab count badge when count is undefined', async () => {
    wrap(<Shell plugins={[pluginWithTabs]} _testHistory={memHistory('/tabbed')} />);
    await waitFor(() => {
      expect(screen.getByTestId('tabbed-content')).toBeInTheDocument();
    });
    // Tab "Three" has no count — no badge
    expect(screen.queryByTestId('tab-count-three')).not.toBeInTheDocument();
  });

  it('renders plugin footer status chips', async () => {
    wrap(<Shell plugins={[pluginWithTabs]} _testHistory={memHistory('/tabbed')} />);
    await waitFor(() => {
      expect(screen.getByTestId('tabbed-content')).toBeInTheDocument();
    });
    expect(screen.getByTestId('footer-status')).toBeInTheDocument();
    expect(screen.getByTestId('tabbed-footer-chip')).toBeInTheDocument();
    expect(screen.getByTestId('tabbed-footer-chip').textContent).toContain('api');
  });

  it('isolates hooks used by plugin slot renderers', () => {
    const ctx = { tweaks: {}, setTweak: () => undefined };
    const view = render(<PluginSlot render={() => HookFooter()} ctx={ctx} />);

    view.rerender(<PluginSlot render={() => DetailedHookFooter()} ctx={ctx} />);

    expect(screen.getByTestId('hook-footer')).toHaveTextContent('connected ready');
  });

  it('collapses subnav when plugin has no subnav', async () => {
    wrap(<Shell plugins={[pluginNoSubnav]} _testHistory={memHistory('/flat')} />);
    await waitFor(() => {
      expect(screen.getByTestId('flat-content')).toBeInTheDocument();
    });
    const subnav = document.querySelector('.niuu-shell__subnav');
    expect(subnav).toBeInTheDocument();
    // No subnav content renders — the :empty CSS pseudo-class collapses the nav
    expect(subnav?.childElementCount).toBe(0);
  });

  it('does not collapse subnav when plugin has subnav', async () => {
    wrap(<Shell plugins={[pluginWithTabs]} _testHistory={memHistory('/tabbed')} />);
    await waitFor(() => {
      expect(screen.getByTestId('tabbed-content')).toBeInTheDocument();
    });
    const subnav = document.querySelector('.niuu-shell__subnav');
    expect(subnav).toBeInTheDocument();
    // Subnav has content rendered by the plugin
    expect(subnav?.childElementCount).toBeGreaterThan(0);
  });

  it('renders subnav element when no subnav content', async () => {
    wrap(<Shell plugins={[pluginNoSubnav]} _testHistory={memHistory('/flat')} />);
    await waitFor(() => {
      expect(screen.getByTestId('flat-content')).toBeInTheDocument();
    });
    const subnav = document.querySelector('.niuu-shell__subnav');
    expect(subnav).toBeInTheDocument();
    // Empty subnav — collapsed via :empty CSS rule
    expect(subnav?.childElementCount).toBe(0);
  });

  it('applies the collapsed subnav class when a plugin toggles it', async () => {
    wrap(
      <Shell plugins={[pluginWithCollapsibleSubnav]} _testHistory={memHistory('/collapsible')} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('collapsible-content')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /collapse subnav/i }));

    await waitFor(() => {
      expect(document.querySelector('.niuu-shell__subnav--collapsed')).toBeInTheDocument();
    });
  });

  it('renders bottom-pinned plugins with icons in the rail, and hides an empty tooltip subtitle', async () => {
    const user = userEvent.setup({ delay: null });
    wrap(
      <Shell plugins={[pluginTopIcon, pluginBottomIcon]} _testHistory={memHistory('/topicon')} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('topicon-content')).toBeInTheDocument();
    });

    // Active top plugin: rendered with both the icon and active rail-item classes.
    const topItem = screen.getByTestId('rail-item-topicon');
    expect(topItem.className).toContain('niuu-shell__rail-item--icon');
    expect(topItem.className).toContain('niuu-shell__rail-item--active');

    // Bottom-pinned plugin: rendered below the spacer, with its icon class, inactive.
    const bottomItem = screen.getByTestId('rail-item-bikon');
    expect(bottomItem).toBeInTheDocument();
    expect(bottomItem.className).toContain('niuu-shell__rail-item--icon');
    expect(bottomItem.className).not.toContain('niuu-shell__rail-item--active');

    // Empty subtitle on the active plugin means the tooltip renders only the title.
    await user.hover(topItem);
    await waitFor(() => {
      expect(screen.getByRole('tooltip')).toBeInTheDocument();
    });
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveTextContent('TopIcon');
    expect(tooltip.querySelector('.niuu-shell__rail-tooltip')?.querySelector('span')).toBeNull();

    // Clicking a bottom-pinned rail item navigates to it, same as a top rail item.
    fireEvent.click(bottomItem);
    await waitFor(() => {
      expect(screen.getByTestId('bikon-content')).toBeInTheDocument();
    });
  });

  it('falls through an earlier plugin whose default tab paths do not match the route', async () => {
    // pluginWithTabs (checked first) has no explicit tab paths, so activePluginId
    // must compute the default `/${pluginId}/${tabId}` path for each of its tabs
    // before concluding none of them match and moving on to pluginA.
    wrap(<Shell plugins={[pluginWithTabs, pluginA]} _testHistory={memHistory('/alpha')} />);
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('tabbed-content')).not.toBeInTheDocument();
  });

  it('renders no active plugin chrome when the route matches no plugin', async () => {
    wrap(<Shell plugins={[]} _testHistory={memHistory('/missing')} />);

    await waitFor(() => {
      expect(screen.getByText('404')).toBeInTheDocument();
    });

    // No active plugin: no title, no tabs, no footer plugin badge.
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
    expect(document.querySelector('.niuu-shell__footer-left code')).not.toBeInTheDocument();
    expect(screen.getByTestId('footer-status')).toBeEmptyDOMElement();
    expect(screen.getByText('0 plugins loaded')).toBeInTheDocument();
    // Nothing was persisted to localStorage since there is no active plugin id.
    expect(localStorage.getItem('niuu.active')).toBeNull();
  });

  it('keeps a plugin visible when its route is active even though simple mode hides it', async () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    wrap(<Shell plugins={[pluginWithSimpleMode, pluginA]} _testHistory={memHistory('/alpha')} />);

    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });
    // Alpha declares no `simple` config, so simple mode would normally hide it —
    // but it stays in the rail because it is the plugin the current route is on.
    expect(screen.getByTestId('rail-item-alpha')).toBeInTheDocument();
  });

  it('switches the color theme from the topbar select', async () => {
    wrap(<Shell plugins={[pluginA]} _testHistory={memHistory('/alpha')} />);
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });

    const select = screen.getByRole('combobox', { name: 'Color theme' });
    fireEvent.change(select, { target: { value: 'amber' } });

    await waitFor(() => {
      expect(document.querySelector('.niuu-shell')).toHaveAttribute('data-theme', 'amber');
    });
  });

  it('switches plugins from the command palette', async () => {
    wrap(<Shell plugins={[pluginA, pluginB]} _testHistory={memHistory('/alpha')} />);
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Open command palette' }));
    const option = await screen.findByRole('option', { name: /Beta/i });
    fireEvent.pointerDown(option);

    await waitFor(() => {
      expect(screen.getByTestId('beta-content')).toBeInTheDocument();
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('navigates and fires onTab when a topbar tab is clicked', async () => {
    const onTab = vi.fn();
    const history = memHistory('/tabclick');
    wrap(<Shell plugins={[{ ...pluginTabClick, onTab }]} _testHistory={history} />);
    await waitFor(() => {
      expect(screen.getByTestId('tabclick-content')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('tabclick-tab-two'));

    expect(onTab).toHaveBeenCalledWith('two');
    await waitFor(() => {
      expect(history.location.pathname).toBe('/tabclick/two');
    });
  });

  it('honors an explicit activeTab override, rendering its rune glyph', async () => {
    wrap(<Shell plugins={[pluginWithActiveTabOverride]} _testHistory={memHistory('/override')} />);
    await waitFor(() => {
      expect(screen.getByTestId('override-content')).toBeInTheDocument();
    });

    // Explicit activeTab='y' wins regardless of the current route.
    expect(screen.getByTestId('override-tab-y').className).toContain('niuu-shell__tab--active');
    expect(screen.getByTestId('override-tab-x').className).not.toContain('niuu-shell__tab--active');
    expect(screen.getByTestId('override-tab-y')).toHaveTextContent('ᚱ');
  });

  it('marks a tab active when its path equals the plugin base route', async () => {
    wrap(<Shell plugins={[pluginWithBaseTab]} _testHistory={memHistory('/dash')} />);
    await waitFor(() => {
      expect(screen.getByTestId('dash-content')).toBeInTheDocument();
    });

    expect(screen.getByTestId('dash-tab-home').className).toContain('niuu-shell__tab--active');
  });

  it('prefers the longer, more specific matching tab path over a shorter prefix', async () => {
    wrap(<Shell plugins={[pluginWithNestedTabs]} _testHistory={memHistory('/nest/over/under')} />);
    await waitFor(() => {
      expect(screen.getByTestId('nest-under-content')).toBeInTheDocument();
    });

    // '/nest/over/under' matches both tab paths as a prefix, but 'under' is the more
    // specific match, so 'over' must not also claim to be active.
    expect(screen.getByTestId('nest-tab-under').className).toContain('niuu-shell__tab--active');
    expect(screen.getByTestId('nest-tab-over').className).not.toContain('niuu-shell__tab--active');
    expect(screen.getByTestId('nest-tab-over')).toHaveTextContent('ᚾ');
  });

  it('surfaces a mode-preference sync error in the footer', async () => {
    const features: IFeatureCatalogService = {
      getFeatureModules: vi.fn().mockResolvedValue([]),
      getUserFeaturePreferences: vi.fn().mockRejectedValue(new Error('preferences unreachable')),
      updateUserFeaturePreferences: vi.fn(),
      toggleFeature: vi.fn(),
    };
    render(
      <ConfigProvider value={{ demoMode: false, theme: 'ice', plugins: {}, services: {} }}>
        <ServicesProvider services={{ features }}>
          <FeatureCatalogProvider>
            <Shell plugins={[pluginA]} _testHistory={memHistory('/alpha')} />
          </FeatureCatalogProvider>
        </ServicesProvider>
      </ConfigProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('alpha-content')).toBeInTheDocument();
    });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('interface preference unavailable');
    expect(alert).toHaveAttribute('title', 'preferences unreachable');
  });
});
