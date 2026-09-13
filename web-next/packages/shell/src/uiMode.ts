import { useSyncExternalStore } from 'react';
import type { UserFeaturePreference } from '@niuulabs/plugin-sdk';

/**
 * Simple / Advanced mode.
 *
 * Simple mode shows only the plugins that declare `simple` on their descriptor, with
 * the tabs they list. Advanced mode is the whole shell. Routes never change.
 *
 * The chosen mode is persisted through the existing per-user feature preferences
 * (`PUT /features/preferences`) under the reserved key `ui.mode`; localStorage only
 * caches it so the first paint after a reload is right.
 */
export type UiMode = 'simple' | 'advanced';

export const UI_MODE_STORAGE_KEY = 'niuu.compactUx.mode';
export const UI_MODE_EVENT = 'niuu:ui-mode';
export const UI_MODE_PREFERENCE_KEY = 'ui.mode';
export const DEFAULT_UI_MODE: UiMode = 'simple';

const MODES: readonly UiMode[] = ['simple', 'advanced'];

export function readUiMode(): UiMode {
  if (typeof window === 'undefined') return DEFAULT_UI_MODE;
  try {
    const value = window.localStorage.getItem(UI_MODE_STORAGE_KEY);
    return value && (MODES as readonly string[]).includes(value)
      ? (value as UiMode)
      : DEFAULT_UI_MODE;
  } catch {
    return DEFAULT_UI_MODE;
  }
}

export function cacheUiMode(mode: UiMode): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(UI_MODE_STORAGE_KEY, mode);
    window.dispatchEvent(new Event(UI_MODE_EVENT));
  } catch {
    // localStorage unavailable; the server-side preference still holds
  }
}

function subscribe(listener: () => void) {
  window.addEventListener('storage', listener);
  window.addEventListener(UI_MODE_EVENT, listener);
  return () => {
    window.removeEventListener('storage', listener);
    window.removeEventListener(UI_MODE_EVENT, listener);
  };
}

export function useUiMode(): UiMode {
  return useSyncExternalStore(subscribe, readUiMode, () => DEFAULT_UI_MODE);
}

/** The mode a saved preference set encodes, or null when none was saved. */
export function uiModeFromPreferences(preferences: UserFeaturePreference[]): UiMode | null {
  const row = preferences.find((preference) => preference.featureKey === UI_MODE_PREFERENCE_KEY);
  if (!row) return null;
  return row.visible ? 'advanced' : 'simple';
}

/** The preference rows that encode a mode for the given plugins (plus the reserved key). */
export function preferencesForMode(
  mode: UiMode,
  plugins: Array<{ id: string; simple?: { tabs?: string[] } }>,
): UserFeaturePreference[] {
  const rows: UserFeaturePreference[] = plugins.map((plugin, index) => ({
    featureKey: plugin.id,
    visible: mode === 'advanced' || plugin.simple !== undefined,
    sortOrder: index,
  }));
  rows.push({
    featureKey: UI_MODE_PREFERENCE_KEY,
    visible: mode === 'advanced',
    sortOrder: plugins.length,
  });
  return rows;
}

export function isVisibleInMode(
  plugin: { simple?: { tabs?: string[] }; position?: 'top' | 'bottom' },
  mode: UiMode,
): boolean {
  if (mode === 'advanced') return true;
  if (plugin.position === 'bottom') return true;
  return plugin.simple !== undefined;
}

export function tabsForMode<T extends { id: string }>(
  plugin: { simple?: { tabs?: string[] }; tabs?: T[] },
  mode: UiMode,
): T[] | undefined {
  if (!plugin.tabs) return undefined;
  if (mode === 'advanced' || !plugin.simple?.tabs) return plugin.tabs;
  const allowed = new Set(plugin.simple.tabs);
  return plugin.tabs.filter((tab) => allowed.has(tab.id));
}
