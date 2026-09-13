import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ServicesProvider, definePlugin, type UserFeaturePreference } from '@niuulabs/plugin-sdk';
import { UiModeSwitch } from './UiModeSwitch';
import { UI_MODE_STORAGE_KEY, readUiMode } from './uiMode';

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
