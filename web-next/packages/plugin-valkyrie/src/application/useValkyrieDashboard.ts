import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ValkyrieDashboard } from '../domain';
import type { AutonomyUpdateRequest, IValkyrieService } from '../ports';

export const VALKYRIE_DASHBOARD_QUERY_KEY = ['valkyrie', 'dashboard'] as const;

export function useValkyrieDashboard() {
  const service = useService<IValkyrieService>('valkyrie');
  return useQuery({
    queryKey: VALKYRIE_DASHBOARD_QUERY_KEY,
    queryFn: () => service.getDashboard(),
    refetchInterval: 10_000,
  });
}

export function useUpdateAutonomy() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: AutonomyUpdateRequest) => service.updateAutonomy(request),
    onSuccess: (dashboard: ValkyrieDashboard) => {
      queryClient.setQueryData(VALKYRIE_DASHBOARD_QUERY_KEY, dashboard);
      void queryClient.invalidateQueries({ queryKey: ['valkyrie', 'reviews'] });
    },
  });
}
