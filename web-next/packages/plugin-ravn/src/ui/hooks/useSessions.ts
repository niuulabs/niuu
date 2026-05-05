import { useQuery, useQueries } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ISessionStream } from '../../ports';

export function useSessions() {
  const service = useService<ISessionStream>('ravn.sessions');
  return useQuery({
    queryKey: ['ravn', 'sessions'],
    queryFn: () => service.listSessions(),
  });
}

export function useSession(id: string) {
  const service = useService<ISessionStream>('ravn.sessions');
  return useQuery({
    queryKey: ['ravn', 'sessions', id],
    queryFn: () => service.getSession(id),
    enabled: !!id,
  });
}

export function useMessages(sessionId: string) {
  const service = useService<ISessionStream>('ravn.sessions');
  return useQuery({
    queryKey: ['ravn', 'messages', sessionId],
    queryFn: () => service.getMessages(sessionId),
    enabled: !!sessionId,
  });
}

/**
 * Aggregates messages from all sessions belonging to a given ravn.
 * Returns messages sorted ascending by timestamp.
 */
export function useRavnActivity(ravnId: string) {
  const service = useService<ISessionStream>('ravn.sessions');

  const sessionsQuery = useQuery({
    queryKey: ['ravn', 'sessions'],
    queryFn: () => service.listSessions(),
  });

  const ravnSessionIds = (sessionsQuery.data ?? [])
    .filter((s) => s.ravnId === ravnId)
    .map((s) => s.id);

  const messageQueries = useQueries({
    queries: ravnSessionIds.map((sessionId) => ({
      queryKey: ['ravn', 'messages', sessionId] as const,
      queryFn: () => service.getMessages(sessionId),
    })),
  });

  const allMessages = messageQueries
    .flatMap((q) => q.data ?? [])
    .sort((a, b) => a.ts.localeCompare(b.ts));

  const isLoading = sessionsQuery.isLoading || messageQueries.some((q) => q.isLoading);

  return { data: allMessages, isLoading };
}
