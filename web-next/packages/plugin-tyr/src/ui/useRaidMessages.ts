import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITyrService, RaidSessionMessage } from '../ports';

export function useRaidMessages(raidIds: string[]) {
  const tyr = useService<ITyrService>('tyr');
  return useQueries({
    queries: raidIds.map((raidId) => ({
      queryKey: ['tyr', 'raids', raidId, 'messages'],
      queryFn: () => tyr.listRaidMessages(raidId),
      enabled: !!raidId,
    })),
  });
}

export function useSendRaidMessage() {
  const tyr = useService<ITyrService>('tyr');
  const queryClient = useQueryClient();

  return useMutation<
    RaidSessionMessage,
    Error,
    { raidId: string; content: string; targetPeerId?: string }
  >({
    mutationFn: ({ raidId, content, targetPeerId }) =>
      tyr.sendRaidMessage(raidId, content, targetPeerId),
    onSuccess: (message, variables) => {
      queryClient.setQueryData<RaidSessionMessage[]>(
        ['tyr', 'raids', variables.raidId, 'messages'],
        (current = []) => [...current, message],
      );
      void queryClient.invalidateQueries({
        queryKey: ['tyr', 'raids', variables.raidId, 'messages'],
      });
    },
  });
}
