import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ValkyrieDashboard } from '../domain';
import type {
  AutonomyUpdateRequest,
  HuddleJoinInput,
  HuddleMessageInput,
  IValkyrieService,
} from '../ports';

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

export function useJoinHuddle() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: HuddleJoinInput) => service.joinHuddle(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: VALKYRIE_DASHBOARD_QUERY_KEY });
    },
  });
}

export function useLeaveHuddle() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (huddleId: string) => service.leaveHuddle(huddleId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: VALKYRIE_DASHBOARD_QUERY_KEY });
    },
  });
}

export function useSendHuddleMessage() {
  const service = useService<IValkyrieService>('valkyrie');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: HuddleMessageInput) => service.sendHuddleMessage(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: VALKYRIE_DASHBOARD_QUERY_KEY });
    },
  });
}
