import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITingService, RunSessionMessage } from '../ports';

export function useRunMessages(runIds: string[]) {
  const ting = useService<ITingService>('ting');
  return useQueries({
    queries: runIds.map((runId) => ({
      queryKey: ['ting', 'runs', runId, 'messages'],
      queryFn: () => ting.listRunMessages(runId),
      enabled: !!runId,
    })),
  });
}

export function useSendRunMessage() {
  const ting = useService<ITingService>('ting');
  const queryClient = useQueryClient();

  return useMutation<
    RunSessionMessage,
    Error,
    { runId: string; content: string; targetPeerId?: string }
  >({
    mutationFn: ({ runId, content, targetPeerId }) =>
      ting.sendRunMessage(runId, content, targetPeerId),
    onSuccess: (message, variables) => {
      queryClient.setQueryData<RunSessionMessage[]>(
        ['ting', 'runs', variables.runId, 'messages'],
        (current = []) => [...current, message],
      );
      void queryClient.invalidateQueries({
        queryKey: ['ting', 'runs', variables.runId, 'messages'],
      });
    },
  });
}
