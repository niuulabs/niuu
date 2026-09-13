import { afterEach, describe, expect, it } from 'vitest';
import {
  UI_MODE_PREFERENCE_KEY,
  UI_MODE_STORAGE_KEY,
  cacheUiMode,
  isVisibleInMode,
  preferencesForMode,
  readUiMode,
  tabsForMode,
  uiModeFromPreferences,
} from './uiMode';

describe('uiMode', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults to simple and reads back what was cached', () => {
    expect(readUiMode()).toBe('simple');
    cacheUiMode('advanced');
    expect(localStorage.getItem(UI_MODE_STORAGE_KEY)).toBe('advanced');
    expect(readUiMode()).toBe('advanced');
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'garbage');
    expect(readUiMode()).toBe('simple');
  });

  it('encodes and decodes the mode through feature preferences', () => {
    const plugins = [{ id: 'realms', simple: {} }, { id: 'observatory' }];
    const advanced = preferencesForMode('advanced', plugins);
    expect(advanced).toEqual([
      { featureKey: 'realms', visible: true, sortOrder: 0 },
      { featureKey: 'observatory', visible: true, sortOrder: 1 },
      { featureKey: UI_MODE_PREFERENCE_KEY, visible: true, sortOrder: 2 },
    ]);
    const simple = preferencesForMode('simple', plugins);
    expect(simple[1]).toEqual({ featureKey: 'observatory', visible: false, sortOrder: 1 });
    expect(uiModeFromPreferences(advanced)).toBe('advanced');
    expect(uiModeFromPreferences(simple)).toBe('simple');
    expect(uiModeFromPreferences([])).toBeNull();
  });

  it('hides undeclared plugins in simple mode but keeps the bottom rail', () => {
    expect(isVisibleInMode({ simple: {} }, 'simple')).toBe(true);
    expect(isVisibleInMode({}, 'simple')).toBe(false);
    expect(isVisibleInMode({ position: 'bottom' }, 'simple')).toBe(true);
    expect(isVisibleInMode({}, 'advanced')).toBe(true);
  });

  it('narrows tabs to the declared set in simple mode only', () => {
    const plugin = {
      simple: { tabs: ['forge'] },
      tabs: [{ id: 'forge' }, { id: 'sessions' }],
    };
    expect(tabsForMode(plugin, 'simple')).toEqual([{ id: 'forge' }]);
    expect(tabsForMode(plugin, 'advanced')).toEqual(plugin.tabs);
    expect(tabsForMode({ simple: {}, tabs: plugin.tabs }, 'simple')).toEqual(plugin.tabs);
    expect(tabsForMode({}, 'simple')).toBeUndefined();
  });
});
