import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import type { ParticipantRole } from '../../models/volundr.model';

/** Where a chat's people are cached, per session. */
export const sessionPeopleKey = (sessionId: string) =>
  ['volundr', 'session-people', sessionId] as const;

/** The signed-in person: who they are, to know which rows are theirs and what they may do. */
export function useViewerIdentity() {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: ['volundr', 'identity'],
    queryFn: () => volundr.getIdentity(),
    staleTime: Infinity,
  });
}

/** Everyone with a grant in this chat: invited and joined. */
export function useSessionParticipants(
  sessionId: string,
  instanceId: string | undefined,
  enabled: boolean,
) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...sessionPeopleKey(sessionId), 'participants'] as const,
    queryFn: ({ signal }) => volundr.getSessionParticipants(sessionId, { instanceId, signal }),
    enabled,
  });
}

/** The session's project members and the room role the project gives each (null: no project). */
export function useSessionProjectPeople(
  sessionId: string,
  instanceId: string | undefined,
  enabled: boolean,
) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...sessionPeopleKey(sessionId), 'project'] as const,
    queryFn: ({ signal }) => volundr.getSessionProjectPeople(sessionId, { instanceId, signal }),
    enabled,
  });
}

/** People of the org to invite, matching what the owner typed. */
export function useParticipantCandidates(
  sessionId: string,
  instanceId: string | undefined,
  query: string,
  enabled: boolean,
) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...sessionPeopleKey(sessionId), 'candidates', query] as const,
    queryFn: ({ signal }) =>
      volundr.getParticipantCandidates(sessionId, query, { instanceId, signal }),
    enabled,
  });
}

/** What changing a chat's people changes: its list, and the session lists that show it. */
function useRefreshPeople(sessionId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: sessionPeopleKey(sessionId) }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      queryClient.invalidateQueries({ queryKey: ['volundr', 'session-list'] }),
    ]);
}

export function useInviteParticipant(sessionId: string, instanceId: string | undefined) {
  const volundr = useService<IVolundrService>('volundr');
  const refresh = useRefreshPeople(sessionId);
  return useMutation({
    mutationFn: (invitation: { userId: string; role: ParticipantRole }) =>
      volundr.inviteSessionParticipant(sessionId, invitation, { instanceId }),
    onSuccess: refresh,
  });
}

/** Remove someone from the chat; for the caller's own id that is declining or leaving. */
export function useRemoveParticipant(sessionId: string, instanceId: string | undefined) {
  const volundr = useService<IVolundrService>('volundr');
  const refresh = useRefreshPeople(sessionId);
  return useMutation({
    mutationFn: (userId: string) =>
      volundr.removeSessionParticipant(sessionId, userId, { instanceId }),
    onSuccess: refresh,
  });
}

/** The invitee joins: accepting turns the invitation into a seat in the chat. */
export function useAcceptInvitation(sessionId: string, instanceId: string | undefined) {
  const volundr = useService<IVolundrService>('volundr');
  const refresh = useRefreshPeople(sessionId);
  return useMutation({
    mutationFn: () => volundr.acceptSessionInvitation(sessionId, { instanceId }),
    onSuccess: refresh,
  });
}
