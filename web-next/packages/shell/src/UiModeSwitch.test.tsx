import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ServicesProvider, definePlugin, type UserFeaturePreference } from '@niuulabs/plugin-sdk';
import { ShellContext, type ShellContextValue } from './ShellContext';
import { UiModeSwitch } from './UiModeSwitch';
import { UI_MODE_STORAGE_KEY, readUiMode, useSetUiMode } from './uiMode';

const plugins = [
  definePlugin({ id: 'realms', rune: 'ᚱ', title: 'Realms', subtitle: '', simple: {} }),
  definePlugin({ id: 'observatory', rune: 'O', title: 'Observatory', subtitle: '' }),
];

function fakeFeatures(saved: UserFeaturePreference[] = [], failUpdate = false) {
  const update = vi.fn(async (preferences: UserFeaturePreference[]) => {
    if (failUpdate) throw new Error('preferences endpoint is down');
    return preferences;
  });
  return {
    service: {
      getFeatureModules: async () => [],
      toggleFeature: async () => {
        throw new Error('not used');
      },
      getUserFeaturePreferences: async () => saved,
      updateUserFeaturePreferences: update,
    },
    update,
  };
}

describe('UiModeSwitch', () => {
  // Start from Simple so each test observes a move to Advanced (the default).
  beforeEach(() => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('stores the mode through the preferences service before flipping', async () => {
    const user = userEvent.setup();
    const { service, update } = fakeFeatures();
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    await waitFor(() => expect(readUiMode()).toBe('advanced'));
    expect(update).toHaveBeenCalledTimes(1);
    const rows = update.mock.calls[0]![0];
    expect(rows.find((row) => row.featureKey === 'ui.mode')?.visible).toBe(true);
    expect(rows.find((row) => row.featureKey === 'observatory')?.visible).toBe(true);
  });

  it('stays where it was and shows the error when the save fails', async () => {
    const user = userEvent.setup();
    const { service } = fakeFeatures([], true);
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    await screen.findByRole('alert');
    expect(readUiMode()).toBe('simple');
  });

  it('lets a saved preference win over the local cache on boot', async () => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
    const { service } = fakeFeatures([{ featureKey: 'ui.mode', visible: true, sortOrder: 0 }]);
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await waitFor(() => expect(readUiMode()).toBe('advanced'));
  });

  it('shows the error when the saved preference cannot be read on boot', async () => {
    const service = {
      ...fakeFeatures().service,
      getUserFeaturePreferences: async () => {
        throw new Error('preferences endpoint is down');
      },
    };
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await screen.findByRole('alert');
    expect(readUiMode()).toBe('simple');
  });

  it('keeps the mode in the browser when the host wires no preferences service', async () => {
    const user = userEvent.setup();
    render(
      <ServicesProvider services={{}}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    await waitFor(() => expect(readUiMode()).toBe('advanced'));
  });
});

describe('useSetUiMode', () => {
  // Start from Simple so each test observes a move to Advanced (the default).
  beforeEach(() => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  function Switcher({ plugins: given }: { plugins?: typeof plugins }) {
    const setUiMode = useSetUiMode();
    const [error, setError] = useState<string | null>(null);
    return (
      <>
        <button
          type="button"
          onClick={() => {
            setUiMode('advanced', given).catch((cause: unknown) => setError(String(cause)));
          }}
        >
          Go advanced
        </button>
        {error ? <span role="alert">{error}</span> : null}
      </>
    );
  }

  it('takes the plugin list from the shell when the caller passes none', async () => {
    const user = userEvent.setup();
    const { service, update } = fakeFeatures();
    const shell: ShellContextValue = {
      enabled: [
        ...plugins,
        definePlugin({ id: 'login', rune: 'L', title: '', subtitle: '', system: true }),
      ],
      brand: null,
      version: '0',
      ctx: { tweaks: {}, setTweak: () => {} },
    };
    render(
      <ServicesProvider services={{ features: service }}>
        <ShellContext.Provider value={shell}>
          <Switcher />
        </ShellContext.Provider>
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Go advanced' }));
    await waitFor(() => expect(readUiMode()).toBe('advanced'));
    const rows = update.mock.calls[0]![0];
    expect(rows.map((row) => row.featureKey)).toEqual(['realms', 'observatory', 'ui.mode']);
  });

  it('raises rather than guessing when there is no shell and no plugin list', async () => {
    const user = userEvent.setup();
    const { service, update } = fakeFeatures();
    render(
      <ServicesProvider services={{ features: service }}>
        <Switcher />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Go advanced' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'useSetUiMode needs the plugin list',
    );
    expect(update).not.toHaveBeenCalled();
    expect(readUiMode()).toBe('simple');
  });
});
