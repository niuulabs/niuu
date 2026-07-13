import { useQuery, useQueries } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ISessionStream } from '../../ports';

const SESSION_PROGRESS_POLL_MS = 2_000;

export function useSessions() {
  const service = useService<ISessionStream>('ravn.sessions');
  return useQuery({
    queryKey: ['ravn', 'sessions'],
    queryFn: () => service.listSessions(),
    refetchInterval: (query) =>
      query.state.data?.some((session) => session.status === 'idle' && !session.chatEndpoint)
        ? SESSION_PROGRESS_POLL_MS
        : false,
  });
}

export function useSession(id: string, instanceId?: string, ravnId?: string) {
  const service = useService<ISessionStream>('ravn.sessions');
  return useQuery({
    queryKey: ['ravn', 'sessions', id, instanceId, ravnId],
    queryFn: () => service.getSession(id, instanceId, ravnId),
    enabled: !!id,
  });
}

export function useMessages(
  sessionId: string,
  enabled = true,
  instanceId?: string,
  ravnId?: string,
) {
  const service = useService<ISessionStream>('ravn.sessions');
  return useQuery({
    queryKey: ['ravn', 'messages', sessionId, instanceId, ravnId],
    queryFn: () => service.getMessages(sessionId, instanceId, ravnId),
    enabled: !!sessionId && enabled,
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

  const ravnSessions = (sessionsQuery.data ?? [])
    .filter((s) => s.ravnId === ravnId && !s.chatEndpoint)
    .map((s) => ({ id: s.id, instanceId: s.instanceId }));

  const messageQueries = useQueries({
    queries: ravnSessions.map((session) => ({
      queryKey: ['ravn', 'messages', session.id, session.instanceId, ravnId] as const,
      queryFn: () => service.getMessages(session.id, session.instanceId, ravnId),
    })),
  });

  const allMessages = messageQueries
    .flatMap((q) => q.data ?? [])
    .sort((a, b) => a.ts.localeCompare(b.ts));

  const isLoading = sessionsQuery.isLoading || messageQueries.some((q) => q.isLoading);

  return { data: allMessages, isLoading };
}
