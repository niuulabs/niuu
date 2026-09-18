import { useCallback, useMemo } from 'react';
import { useForgePreference } from './useForgePreference';

/** Ordered browser-local favourites, independent of session lifecycle and grouping. */
export function usePinnedSessions() {
  const [stored, setStored] = useForgePreference<string>('pinnedSessions', '[]');
  const ids = useMemo<string[]>(() => {
    try {
      const value: unknown = JSON.parse(stored);
      if (!Array.isArray(value)) return [];
      return [...new Set(value.filter((id): id is string => typeof id === 'string' && !!id))];
    } catch {
      return [];
    }
  }, [stored]);
  const pinned = useMemo(() => new Set(ids), [ids]);
  const toggle = useCallback(
    (sessionId: string) => {
      const next = pinned.has(sessionId)
        ? ids.filter((id) => id !== sessionId)
        : [...ids, sessionId];
      setStored(JSON.stringify(next));
    },
    [ids, pinned, setStored],
  );
  return { ids, pinned, toggle };
}
