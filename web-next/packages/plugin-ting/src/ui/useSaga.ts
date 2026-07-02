import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITingService, SagaRepoRef, SagaTargetSelection } from '../ports';
import type { Saga } from '../domain/saga';

export function useSaga(id: string) {
  const ting = useService<ITingService>('ting');
  return useQuery({
    queryKey: ['ting', 'sagas', id],
    queryFn: () => ting.getSaga(id),
    enabled: !!id,
  });
}

export function useAssignSagaWorkflow(sagaId: string) {
  const ting = useService<ITingService>('ting');
  const queryClient = useQueryClient();

  return useMutation<Saga, Error, string | null>({
    mutationFn: (workflowId: string | null) => ting.assignWorkflow(sagaId, workflowId),
    onSuccess: (saga) => {
      queryClient.setQueryData(['ting', 'sagas', saga.id], saga);
      queryClient.setQueryData(['ting', 'sagas'], (current: Saga[] | undefined) => {
        if (!Array.isArray(current)) return current;
        return current.map((entry) => (entry.id === saga.id ? saga : entry));
      });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'dispatch-queue'] });
    },
  });
}

export function useAssignSagaTarget(sagaId: string) {
  const ting = useService<ITingService>('ting');
  const queryClient = useQueryClient();

  return useMutation<Saga, Error, SagaTargetSelection>({
    mutationFn: (target: SagaTargetSelection) => ting.assignTarget(sagaId, target),
    onSuccess: (saga) => {
      queryClient.setQueryData(['ting', 'sagas', saga.id], saga);
      queryClient.setQueryData(['ting', 'sagas'], (current: Saga[] | undefined) => {
        if (!Array.isArray(current)) return current;
        return current.map((entry) => (entry.id === saga.id ? saga : entry));
      });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'dispatch-queue'] });
    },
  });
}

export function useAssignSagaRepos(sagaId: string) {
  const ting = useService<ITingService>('ting');
  const queryClient = useQueryClient();

  return useMutation<Saga, Error, SagaRepoRef[]>({
    mutationFn: (repoRefs: SagaRepoRef[]) => ting.assignRepos(sagaId, repoRefs),
    onSuccess: (saga) => {
      queryClient.setQueryData(['ting', 'sagas', saga.id], saga);
      queryClient.setQueryData(['ting', 'sagas'], (current: Saga[] | undefined) => {
        if (!Array.isArray(current)) return current;
        return current.map((entry) => (entry.id === saga.id ? saga : entry));
      });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'dispatch-queue'] });
    },
  });
}
