import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { ServicesProvider, definePlugin, type UserFeaturePreference } from '@niuulabs/plugin-sdk';
import { ShellContext, type ShellContextValue } from './ShellContext';
import { UiModeSwitch } from './UiModeSwitch';
import {
  UI_MODE_STORAGE_KEY,
  readUiMode,
  useSetUiMode,
  useUiMode,
  useUiModePreferenceSync,
} from './uiMode';

const plugins = [
  definePlugin({ id: 'realms', rune: 'ᚱ', title: 'Realms', subtitle: '', simple: {} }),
  definePlugin({ id: 'observatory', rune: 'O', title: 'Observatory', subtitle: '' }),
];

function fakeFeatures(saved: UserFeaturePreference[] = [], failUpdate = false, failRead = false) {
  const read = vi.fn(async () => {
    if (failRead) throw new Error('preferences endpoint is down');
    return saved;
  });
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
      getUserFeaturePreferences: read,
      updateUserFeaturePreferences: update,
    },
    read,
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
    const { service, read, update } = fakeFeatures();
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    await waitFor(() => expect(readUiMode()).toBe('advanced'));
    expect(read).not.toHaveBeenCalled();
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

  it('does nothing when the currently active mode is clicked again', async () => {
    const user = userEvent.setup();
    const { service, update } = fakeFeatures();
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Simple' }));
    expect(update).not.toHaveBeenCalled();
    expect(readUiMode()).toBe('simple');
  });

  it('renders a message from a non-Error rejection', async () => {
    const user = userEvent.setup();
    const service = {
      getFeatureModules: async () => [],
      toggleFeature: async () => {
        throw new Error('not used');
      },
      getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
      updateUserFeaturePreferences: vi.fn().mockRejectedValue('offline'),
    };
    render(
      <ServicesProvider services={{ features: service }}>
        <UiModeSwitch plugins={plugins} />
      </ServicesProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Advanced' }));
    expect(await screen.findByRole('alert')).toHaveAttribute('title', 'offline');
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

describe('useUiModePreferenceSync', () => {
  beforeEach(() => {
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'simple');
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  function HydrationProbe() {
    const error = useUiModePreferenceSync();
    const mode = useUiMode();
    return (
      <>
        <output aria-label="Hydrated interface mode">{mode}</output>
        {error ? <span role="alert">{error}</span> : null}
      </>
    );
  }

  it('hydrates the cached mode without mounting the mode control', async () => {
    const { service, read, update } = fakeFeatures([
      { featureKey: 'ui.mode', visible: true, sortOrder: 0 },
    ]);
    render(
      <ServicesProvider services={{ features: service }}>
        <HydrationProbe />
      </ServicesProvider>,
    );

    expect(screen.queryByTestId('ui-mode-switch')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('status', { name: 'Hydrated interface mode' })).toHaveTextContent(
        'advanced',
      ),
    );
    expect(read).toHaveBeenCalledTimes(1);
    expect(update).not.toHaveBeenCalled();
  });

  it('reports hydration failure and retains the cached mode', async () => {
    const { service, read } = fakeFeatures([], false, true);
    render(
      <ServicesProvider services={{ features: service }}>
        <HydrationProbe />
      </ServicesProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('preferences endpoint is down');
    expect(screen.getByRole('status', { name: 'Hydrated interface mode' })).toHaveTextContent(
      'simple',
    );
    expect(read).toHaveBeenCalledTimes(1);
    expect(readUiMode()).toBe('simple');
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
