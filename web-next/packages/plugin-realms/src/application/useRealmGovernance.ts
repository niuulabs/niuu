import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRealmGovernanceService, TrustGrantCreate } from '../ports';

/** The service key the host registers the realm governance adapter under. */
export const REALM_GOVERNANCE_SERVICE_KEY = 'realms.governance';

export const REALMS_QUERY_KEY = ['realms'] as const;

export function useRealms() {
  const service = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
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
  const service = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
  return useQuery({
    queryKey: [...REALMS_QUERY_KEY, slug, 'trust-grants'],
    queryFn: () => service.listTrustGrants(slug),
    enabled: enabled && slug.length > 0,
  });
}

export function useCreateTrustGrant(slug: string) {
  const service = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
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
