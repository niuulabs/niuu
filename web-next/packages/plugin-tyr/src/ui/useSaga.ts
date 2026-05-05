import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITyrService } from '../ports';
import type { Saga } from '../domain/saga';

export function useSaga(id: string) {
  const tyr = useService<ITyrService>('tyr');
  return useQuery({
    queryKey: ['tyr', 'sagas', id],
    queryFn: () => tyr.getSaga(id),
    enabled: !!id,
  });
}

export function useAssignSagaWorkflow(sagaId: string) {
  const tyr = useService<ITyrService>('tyr');
  const queryClient = useQueryClient();

  return useMutation<Saga, Error, string | null>({
    mutationFn: (workflowId: string | null) => tyr.assignWorkflow(sagaId, workflowId),
    onSuccess: (saga) => {
      queryClient.setQueryData(['tyr', 'sagas', saga.id], saga);
      queryClient.setQueryData(['tyr', 'sagas'], (current: Saga[] | undefined) => {
        if (!Array.isArray(current)) return current;
        return current.map((entry) => (entry.id === saga.id ? saga : entry));
      });
      void queryClient.invalidateQueries({ queryKey: ['tyr', 'dispatch-queue'] });
    },
  });
}
