import { useCallback, useMemo } from 'react';
import { parseNotificationFilter, type NotificationFilter } from '../../domain/notifications';
import { useForgePreference } from '../useForgePreference';

/** The feed filter, persisted per browser like other Forge preferences. */
export function useNotificationFilter(): readonly [
  NotificationFilter,
  (next: NotificationFilter) => void,
] {
  const [raw, setRaw] = useForgePreference<string>('notifications.filter', '');
  const filter = useMemo(() => parseNotificationFilter(raw), [raw]);
  const setFilter = useCallback(
    (next: NotificationFilter) => setRaw(JSON.stringify(next)),
    [setRaw],
  );
  return [filter, setFilter] as const;
}
