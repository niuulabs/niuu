import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { createMemoryHistory } from '@tanstack/react-router';
import {
  ConfigProvider,
  FeatureCatalogProvider,
  ServicesProvider,
  definePlugin,
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
  simple: { tabs: ['forge'] },
  tabs: [
    { id: 'forge', label: 'Forge' },
    { id: 'catalog', label: 'Catalog' },
  ],
  render: () => <div data-testid="volundr-content">volundr</div>,
});

const observatory = definePlugin({
  id: 'observatory',
  rune: 'O',
  title: 'Observatory',
  subtitle: 'advanced only',
  render: () => <div data-testid="observatory-content">observatory</div>,
});

function wrap(path: string) {
  return render(
    <ConfigProvider value={{ demoMode: false, theme: 'ice', plugins: {}, services: {} }}>
      <ServicesProvider services={{}}>
        <FeatureCatalogProvider>
          <Shell
            plugins={[realms, volundr, observatory]}
            _testHistory={createMemoryHistory({ initialEntries: [path] })}
          />
        </FeatureCatalogProvider>
      </ServicesProvider>
    </ConfigProvider>,
  );
}

describe('Shell in simple mode', () => {
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
    expect(screen.queryByTestId('volundr-tab-catalog')).not.toBeInTheDocument();
    expect(screen.getByTestId('ui-mode-switch')).toHaveAttribute('data-mode', 'simple');
  });

  it('keeps a hidden plugin reachable by deep link, with its rail item while active', async () => {
    wrap('/observatory');
    await screen.findByTestId('observatory-content');
    expect(screen.getByRole('button', { name: 'Observatory' })).toBeInTheDocument();
  });

  it('shows everything in advanced mode', async () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'advanced');
    wrap('/volundr');
    await screen.findByTestId('volundr-content');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Observatory' })).toBeInTheDocument(),
    );
    expect(screen.getByTestId('volundr-tab-catalog')).toBeInTheDocument();
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
});
