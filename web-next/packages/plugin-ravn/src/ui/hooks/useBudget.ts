import { useMemo } from 'react';
import { useQuery, useQueries } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IBudgetStream } from '../../ports';
import type { BudgetState } from '@niuulabs/domain';

export function useFleetBudget() {
  const service = useService<IBudgetStream>('ravn.budget');
  return useQuery({
    queryKey: ['ravn', 'budget', 'fleet'],
    queryFn: () => service.getFleetBudget(),
  });
}

export function useRavnBudget(ravnId: string) {
  const service = useService<IBudgetStream>('ravn.budget');
  return useQuery({
    queryKey: ['ravn', 'budget', ravnId],
    queryFn: () => service.getBudget(ravnId),
    enabled: !!ravnId,
  });
}

/**
 * Fetch budgets for many ravens in parallel.
 * Returns a Record<ravnId, BudgetState> once all queries resolve.
 */
export function useRavnBudgets(ravnIds: string[]): Record<string, BudgetState> {
  const service = useService<IBudgetStream>('ravn.budget');

  const results = useQueries({
    queries: ravnIds.map((id) => ({
      queryKey: ['ravn', 'budget', id],
      queryFn: () => service.getBudget(id),
    })),
  });

  return useMemo(() => {
    const budgets: Record<string, BudgetState> = {};
    for (let i = 0; i < ravnIds.length; i++) {
      const id = ravnIds[i];
      const data = results[i]?.data;
      if (id && data) budgets[id] = data;
    }
    return budgets;
  }, [results, ravnIds]);
}
