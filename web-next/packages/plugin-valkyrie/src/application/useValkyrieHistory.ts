import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { DecisionListFilters, IValkyrieService, SignalHistoryFilters } from '../ports';

export const VALKYRIE_HISTORY_QUERY_KEY = ['valkyrie', 'history'] as const;

export function useDecisionList(filters: DecisionListFilters = {}, enabled = true) {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: [...VALKYRIE_HISTORY_QUERY_KEY, 'decisions', filters],
    queryFn: () => service.listDecisions(filters),
    refetchInterval: 10_000,
    enabled,
  });
}

export function useDecisionDetail(decisionId: string | null) {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: [...VALKYRIE_HISTORY_QUERY_KEY, 'decision', decisionId],
    queryFn: () => service.getDecision(decisionId ?? ''),
    enabled: Boolean(decisionId),
  });
}

export function useSignalHistory(filters: SignalHistoryFilters = {}, enabled = true) {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: [...VALKYRIE_HISTORY_QUERY_KEY, 'signals', filters],
    queryFn: () => service.listSignalHistory(filters),
    refetchInterval: 10_000,
    enabled,
  });
}

export function useSkillStats(environmentId?: string) {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: [...VALKYRIE_HISTORY_QUERY_KEY, 'skills', environmentId ?? ''],
    queryFn: () => service.getSkillStats(environmentId),
    refetchInterval: 30_000,
  });
}
