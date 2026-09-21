import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { createMemoryHistory } from '@tanstack/react-router';
import {
  ConfigProvider,
  FeatureCatalogProvider,
  ServicesProvider,
  definePlugin,
  type UserFeaturePreference,
} from '@niuulabs/plugin-sdk';
import { Shell } from './Shell';
import { UI_MODE_STORAGE_KEY } from './uiMode';

const realms = definePlugin({
  id: 'realms',
  rune: 'ᚱ',
  title: 'Realms',
  subtitle: 'simple-first',
  simple: {},
  render: () => <div data-testid="realms-content">realms</div>,
});

const volundr = definePlugin({
  id: 'volundr',
  rune: 'V',
  title: 'Völundr',
  subtitle: 'forge',
  simple: { tabs: ['forge', 'quick'], title: 'Sessions', icon: <span>icon</span> },
  tabs: [
    { id: 'forge', label: 'Forge' },
    { id: 'catalog', label: 'Catalog' },
    { id: 'quick', label: 'Quick', simpleOnly: true },
  ],
  render: () => <div data-testid="volundr-content">volundr</div>,
});

const home = definePlugin({
  id: 'home',
  rune: 'H',
  title: 'Home',
  subtitle: 'simple only',
  simple: { only: true, landing: true },
  render: () => <div data-testid="home-content">home</div>,
});

const observatory = definePlugin({
  id: 'observatory',
  rune: 'O',
  title: 'Observatory',
  subtitle: 'advanced only',
  render: () => <div data-testid="observatory-content">observatory</div>,
});

function featureService(saved: UserFeaturePreference[] | Error) {
  return {
    getFeatureModules: async () => [],
    toggleFeature: async () => {
      throw new Error('not used');
    },
    getUserFeaturePreferences: async () => {
      if (saved instanceof Error) throw saved;
      return saved;
    },
    updateUserFeaturePreferences: async (preferences: UserFeaturePreference[]) => preferences,
  };
}

function wrap(path: string, features?: ReturnType<typeof featureService>) {
  return render(
    <ConfigProvider value={{ demoMode: false, theme: 'ice', plugins: {}, services: {} }}>
      <ServicesProvider services={features ? { features } : {}}>
        <FeatureCatalogProvider>
          <Shell
            plugins={[home, realms, volundr, observatory]}
            _testHistory={createMemoryHistory({ initialEntries: [path] })}
          />
        </FeatureCatalogProvider>
      </ServicesProvider>
    </ConfigProvider>,
  );
}

describe('Shell in simple mode', () => {
  // Advanced is the default; these tests opt into Simple the way a person does.
  beforeEach(() => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('hides undeclared plugins from the rail and narrows the tabs', async () => {
    wrap('/volundr');
    await screen.findByTestId('volundr-content');
    expect(screen.getByRole('button', { name: 'Realms' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Observatory' })).not.toBeInTheDocument();
    expect(screen.getByTestId('volundr-tab-forge')).toBeInTheDocument();
    expect(screen.getByTestId('volundr-tab-quick')).toBeInTheDocument();
    expect(screen.queryByTestId('volundr-tab-catalog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('ui-mode-switch')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sessions' })).toBeInTheDocument();
    // The rail item stays keyed on the plugin id even when the mode renames it,
    // so hosts outside the monorepo can find it without knowing the mode.
    expect(screen.getByTestId('rail-item-volundr')).toHaveAttribute('aria-label', 'Sessions');
    expect(screen.getByRole('heading', { level: 1, name: 'Sessions' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Home' })).toBeInTheDocument();
  });

  it('lands on the home plugin from the index route', async () => {
    wrap('/');
    await screen.findByTestId('home-content');
  });

  it('keeps a hidden plugin reachable by deep link, with its rail item while active', async () => {
    wrap('/observatory');
    await screen.findByTestId('observatory-content');
    expect(screen.getByRole('button', { name: 'Observatory' })).toBeInTheDocument();
  });

  it('starts in advanced mode when no mode has been chosen', async () => {
    localStorage.removeItem(UI_MODE_STORAGE_KEY);
    wrap('/volundr');
    await screen.findByTestId('volundr-content');
    expect(screen.queryByTestId('ui-mode-switch')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Observatory' })).toBeInTheDocument();
    expect(screen.getByTestId('volundr-tab-catalog')).toBeInTheDocument();
  });

  it('shows everything in advanced mode', async () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    wrap('/volundr');
    await screen.findByTestId('volundr-content');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Observatory' })).toBeInTheDocument(),
    );
    expect(screen.getByTestId('volundr-tab-catalog')).toBeInTheDocument();
    expect(screen.queryByTestId('volundr-tab-quick')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Home' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Völundr' })).toBeInTheDocument();
  });

  it('offers no switch when no plugin opted into simple mode', async () => {
    render(
      <ConfigProvider value={{ demoMode: false, theme: 'ice', plugins: {}, services: {} }}>
        <ServicesProvider services={{}}>
          <FeatureCatalogProvider>
            <Shell
              plugins={[observatory]}
              _testHistory={createMemoryHistory({ initialEntries: ['/observatory'] })}
            />
          </FeatureCatalogProvider>
        </ServicesProvider>
      </ConfigProvider>,
    );
    await screen.findByTestId('observatory-content');
    expect(screen.queryByTestId('ui-mode-switch')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Observatory' })).toBeInTheDocument();
  });

  it('hydrates the saved mode at shell boot without mounting the settings control', async () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    wrap('/volundr', featureService([{ featureKey: 'ui.mode', visible: false, sortOrder: 0 }]));
    await waitFor(() => expect(localStorage.getItem(UI_MODE_STORAGE_KEY)).toBe('simple'));
    expect(screen.queryByRole('button', { name: 'Observatory' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('ui-mode-switch')).not.toBeInTheDocument();
  });

  it('reports preference hydration failures and retains the cached mode', async () => {
    wrap('/volundr', featureService(new Error('preferences endpoint is down')));
    expect(await screen.findByRole('alert')).toHaveTextContent('interface preference unavailable');
    expect(localStorage.getItem(UI_MODE_STORAGE_KEY)).toBe('simple');
  });
});
