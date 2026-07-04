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

/**
 * Trust grants for a realm. Pass `enabled: false` when the caller already knows
 * the realm does not exist (see `useRealms`) so we never fire a request that
 * would 404 for an environment with no realm configured.
 */
export function useRealmTrustGrants(slug: string, enabled = true) {
  const service = useService<IRealmGovernanceService>('valkyrie.realms');
  return useQuery({
    queryKey: [...REALMS_QUERY_KEY, slug, 'trust-grants'],
    queryFn: () => service.listTrustGrants(slug),
    enabled: enabled && slug.length > 0,
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
