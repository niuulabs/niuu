import { useCallback, useSyncExternalStore } from 'react';

const transient = new Map<string, string>();
const CHANGE_EVENT = 'niuu:forge-preferences';

function subscribe(notify: () => void) {
  window.addEventListener('storage', notify);
  window.addEventListener(CHANGE_EVENT, notify);
  return () => {
    window.removeEventListener('storage', notify);
    window.removeEventListener(CHANGE_EVENT, notify);
  };
}

/** Preferences are optional browser-local presentation state, never session state. */
export function useForgePreference<T extends string>(
  name: string,
  initial: T,
  allowed?: readonly T[],
): readonly [T, (value: T) => void] {
  const key = `niuu.forge.${name}`;
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
