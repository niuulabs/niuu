import { afterEach, describe, expect, it } from 'vitest';
import {
  UI_MODE_PREFERENCE_KEY,
  UI_MODE_STORAGE_KEY,
  cacheUiMode,
  isVisibleInMode,
  landingPluginId,
  pluginFace,
  preferencesForMode,
  readUiMode,
  tabsForMode,
  uiModeFromPreferences,
} from './uiMode';

describe('uiMode', () => {
  afterEach(() => {
    localStorage.clear();
  });

  it('defaults to advanced and reads back what was cached', () => {
    expect(readUiMode()).toBe('advanced');
    cacheUiMode('simple');
    expect(localStorage.getItem(UI_MODE_STORAGE_KEY)).toBe('simple');
    expect(readUiMode()).toBe('simple');
    localStorage.setItem(UI_MODE_STORAGE_KEY, 'garbage');
    expect(readUiMode()).toBe('advanced');
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

  it('hides simple-only plugins and tabs in advanced mode', () => {
    expect(isVisibleInMode({ simple: { only: true } }, 'advanced')).toBe(false);
    expect(isVisibleInMode({ simple: { only: true } }, 'simple')).toBe(true);
    const tabs = [{ id: 'ravens' }, { id: 'residents', simpleOnly: true }];
    expect(tabsForMode({ simple: { tabs: ['residents'] }, tabs }, 'simple')).toEqual([tabs[1]]);
    expect(tabsForMode({ simple: { tabs: ['residents'] }, tabs }, 'advanced')).toEqual([tabs[0]]);
  });

  it('gives a plugin its simple-mode face and finds the landing plugin', () => {
    const plugin = {
      id: 'volundr',
      rune: 'V',
      title: 'Völundr',
      subtitle: 'forge',
      simple: { title: 'Sessions', icon: 'icon', landing: false },
    };
    expect(pluginFace(plugin, 'advanced')).toEqual({
      title: 'Völundr',
      subtitle: 'forge',
      glyph: 'V',
    });
    expect(pluginFace(plugin, 'simple')).toEqual({
      title: 'Sessions',
      subtitle: 'forge',
      glyph: 'icon',
    });
    expect(pluginFace({ ...plugin, simple: undefined }, 'simple').glyph).toBe('V');
    const home = { id: 'home', simple: { landing: true } };
    expect(landingPluginId([plugin, home], 'simple')).toBe('home');
    expect(landingPluginId([plugin, home], 'advanced')).toBeNull();
    expect(landingPluginId([plugin], 'simple')).toBeNull();
  });
});
