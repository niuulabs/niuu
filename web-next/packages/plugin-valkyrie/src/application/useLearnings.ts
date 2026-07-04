import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { LearningRecord } from '../domain';
import type {
  IValkyrieService,
  LearningFeedbackInput,
  LearningRevisionInput,
  LearningRevisionResult,
} from '../ports';
import { VALKYRIE_DASHBOARD_QUERY_KEY } from './useValkyrieDashboard';

export const VALKYRIE_LEARNING_QUERY_KEY = ['valkyrie', 'learning'] as const;

/** One learning's full record; null when unknown (404). */
export function useLearning(learningId: string | null) {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: [...VALKYRIE_LEARNING_QUERY_KEY, learningId ?? ''],
    queryFn: () => service.getLearning(learningId ?? ''),
    enabled: Boolean(learningId),
  });
}

export function useSendLearningFeedback() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: LearningFeedbackInput) => service.sendLearningFeedback(request),
    onSuccess: (learning: LearningRecord) => {
      queryClient.setQueryData([...VALKYRIE_LEARNING_QUERY_KEY, learning.id], learning);
      void queryClient.invalidateQueries({ queryKey: VALKYRIE_DASHBOARD_QUERY_KEY });
    },
  });
}

export function useReviseLearning() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: LearningRevisionInput) => service.reviseLearning(request),
    onSuccess: (result: LearningRevisionResult) => {
      queryClient.setQueryData(
        [...VALKYRIE_LEARNING_QUERY_KEY, result.learning.id],
        result.learning,
      );
      void queryClient.invalidateQueries({ queryKey: VALKYRIE_DASHBOARD_QUERY_KEY });
    },
  });
}
