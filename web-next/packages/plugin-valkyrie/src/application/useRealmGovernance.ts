import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRealmGovernanceService, TrustGrantCreate } from '../ports';

export const REALMS_QUERY_KEY = ['valkyrie', 'realms'] as const;
export const TOOL_WORKFLOWS_QUERY_KEY = ['valkyrie', 'tool-workflows'] as const;

export function useRealms() {
  const service = useService<IRealmGovernanceService>('valkyrie.realms');
  return useQuery({
    queryKey: REALMS_QUERY_KEY,
    queryFn: () => service.listRealms(),
  });
}

export function useRealmTrustGrants(slug: string) {
  const service = useService<IRealmGovernanceService>('valkyrie.realms');
  return useQuery({
    queryKey: [...REALMS_QUERY_KEY, slug, 'trust-grants'],
    queryFn: () => service.listTrustGrants(slug),
  });
}

export function useToolWorkflows() {
  const service = useService<IRealmGovernanceService>('valkyrie.realms');
  return useQuery({
    queryKey: TOOL_WORKFLOWS_QUERY_KEY,
    queryFn: () => service.listWorkflows(),
  });
}

export function useCreateTrustGrant(slug: string) {
  const service = useService<IRealmGovernanceService>('valkyrie.realms');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: TrustGrantCreate) => service.createTrustGrant(slug, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: [...REALMS_QUERY_KEY, slug, 'trust-grants'],
      });
    },
  });
}
