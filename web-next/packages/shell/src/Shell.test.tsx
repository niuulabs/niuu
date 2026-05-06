import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { createMemoryHistory } from '@tanstack/react-router';
import { ConfigProvider, FeatureCatalogProvider, definePlugin } from '@niuulabs/plugin-sdk';
import { Shell } from './Shell';

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

// Plugin without subnav — tests collapse
const pluginNoSubnav = definePlugin({
  id: 'flat',
  rune: 'ᚠ',
  title: 'Flat',
  subtitle: 'no subnav',
  render: () => <div data-testid="flat-content">flat-rendered</div>,
});

function wrap(
  ui: React.ReactNode,
  pluginOverrides: Record<string, { enabled: boolean; order: number }> = {},
) {
  return render(
    <ConfigProvider
      value={{
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
});
