import { useCallback, useSyncExternalStore } from 'react';

const transient = new Map<string, string>();
const CHANGE_EVENT = 'niuu:preferences';

function subscribe(notify: () => void) {
  window.addEventListener('storage', notify);
  window.addEventListener(CHANGE_EVENT, notify);
  return () => {
    window.removeEventListener('storage', notify);
    window.removeEventListener(CHANGE_EVENT, notify);
  };
}

/**
 * A browser-local presentation preference under `key`, shared by every component that
 * reads it. Never session state: a browser that refuses storage keeps it for the tab.
 */
export function useStoredPreference<T extends string>(
  key: string,
  initial: T,
  allowed?: readonly T[],
): readonly [T, (value: T) => void] {
  const read = useCallback(() => {
    try {
      const value = transient.get(key) ?? window.localStorage.getItem(key);
      return value !== null && (!allowed || allowed.includes(value as T)) ? (value as T) : initial;
    } catch {
      return (transient.get(key) as T | undefined) ?? initial;
    }
  }, [key, initial, allowed]);
  const value = useSyncExternalStore(subscribe, read, () => initial);
  const set = useCallback(
    (next: T) => {
      try {
        window.localStorage.setItem(key, next);
        transient.delete(key);
      } catch {
        // Keep controls usable when this browser disables persistent storage.
        transient.set(key, next);
      }
      window.dispatchEvent(new Event(CHANGE_EVENT));
    },
    [key],
  );
  return [value, set];
}
