import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from './ConfigProvider';
import { FeatureCatalogProvider, useFeatureCatalog } from './FeatureCatalog';
import type { IFeatureCatalogService, FeatureModule } from './ports/feature-catalog.port';

function Reader({ id }: { id: string }) {
  const { isEnabled, order } = useFeatureCatalog();
  return (
    <span data-testid="out">
      {String(isEnabled(id))}-{order(id)}
    </span>
  );
}

describe('FeatureCatalog — config-only (no service)', () => {
  it('reads enabled + order from config', () => {
    render(
      <ConfigProvider
        value={{
          theme: 'ice',
          plugins: {
            tyr: { enabled: true, order: 4 },
            mimir: { enabled: false, order: 2 },
          },
          services: {},
        }}
      >
        <FeatureCatalogProvider>
          <Reader id="tyr" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );
    expect(screen.getByTestId('out').textContent).toBe('true-4');
  });

  it('defaults order to 100 and enabled to true when plugin is absent', () => {
    render(
      <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
        <FeatureCatalogProvider>
          <Reader id="missing" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );
    expect(screen.getByTestId('out').textContent).toBe('true-100');
  });

  it('respects overrides', () => {
    render(
      <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
        <FeatureCatalogProvider overrides={{ isEnabled: () => false, order: () => 42 }}>
          <Reader id="anything" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );
    expect(screen.getByTestId('out').textContent).toBe('false-42');
  });

  it('throws when used outside the provider', () => {
    const error = console.error;
    console.error = () => {};
    expect(() =>
      render(
        <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
          <Reader id="x" />
        </ConfigProvider>,
      ),
    ).toThrow(/FeatureCatalogProvider/);
    console.error = error;
  });
});

describe('FeatureCatalog — service-driven', () => {
  function makeService(modules: FeatureModule[]): IFeatureCatalogService {
    return {
      getFeatureModules: vi.fn().mockResolvedValue(modules),
      getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
      updateUserFeaturePreferences: vi.fn().mockResolvedValue([]),
      toggleFeature: vi.fn(),
    };
  }

  it('uses service module data when service resolves', async () => {
    const service = makeService([
      {
        key: 'users',
        label: 'Users',
        icon: 'Users',
        scope: 'admin',
        enabled: false,
        defaultEnabled: true,
        adminOnly: true,
        order: 99,
      },
    ]);

    render(
      <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
        <FeatureCatalogProvider service={service}>
          <Reader id="users" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('out').textContent).toBe('false-99'));
  });

  it('falls back to config when service key is not in modules', async () => {
    const service = makeService([]);

    render(
      <ConfigProvider
        value={{ theme: 'ice', plugins: { tyr: { enabled: true, order: 4 } }, services: {} }}
      >
        <FeatureCatalogProvider service={service}>
          <Reader id="tyr" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );

    // Service resolves with empty list; tyr falls back to config
    await waitFor(() => expect(screen.getByTestId('out').textContent).toBe('true-4'));
  });

  it('falls back gracefully when service rejects', async () => {
    const service: IFeatureCatalogService = {
      getFeatureModules: vi.fn().mockRejectedValue(new Error('unavailable')),
      getUserFeaturePreferences: vi.fn(),
      updateUserFeaturePreferences: vi.fn(),
      toggleFeature: vi.fn(),
    };

    render(
      <ConfigProvider
        value={{ theme: 'ice', plugins: { hello: { enabled: true, order: 1 } }, services: {} }}
      >
        <FeatureCatalogProvider service={service}>
          <Reader id="hello" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );

    // Config fallback is immediate; service error is swallowed
    expect(screen.getByTestId('out').textContent).toBe('true-1');
  });

  it('calls getFeatureModules on mount', async () => {
    const service = makeService([]);

    render(
      <ConfigProvider value={{ theme: 'ice', plugins: {}, services: {} }}>
        <FeatureCatalogProvider service={service}>
          <Reader id="x" />
        </FeatureCatalogProvider>
      </ConfigProvider>,
    );

    await waitFor(() => expect(service.getFeatureModules).toHaveBeenCalledOnce());
  });
});
