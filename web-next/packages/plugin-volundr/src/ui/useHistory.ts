import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { useMemo } from 'react';
import type { ISessionStore } from '../ports/ISessionStore';
import { applyHistoryFilters } from '../application/historyFilter';
import type { HistoryFilters, HistoryOutcome } from '../application/historyFilter';

export type { HistoryFilters, HistoryOutcome };

/**
 * Queries the session store for all sessions, then filters to only
 * terminated/failed sessions matching the supplied criteria.
 *
 * Each filter is a primitive value so the useMemo dependency array is stable.
 */
export function useHistory({
  ravnId,
  personaName,
  sagaId,
  outcome,
  dateFrom,
  dateTo,
}: HistoryFilters = {}) {
  const store = useService<ISessionStore>('volundr.sessions');
  const query = useQuery({
    queryKey: ['volundr', 'history'],
    queryFn: () => store.listSessions(),
  });

  const filtered = useMemo(
    () =>
      query.data
        ? applyHistoryFilters(query.data, {
            ravnId,
            personaName,
            sagaId,
            outcome,
            dateFrom,
            dateTo,
          })
        : [],
    [query.data, ravnId, personaName, sagaId, outcome, dateFrom, dateTo],
  );

  return { ...query, data: filtered };
}
