import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { MemoryProposal } from '../domain/memoryGateway';
import { MEMORY_GATEWAY_SERVICE_KEY, type IMemoryGatewayService } from '../ports/IMemoryGateway';

/**
 * Search every memory the person reads for `query`; one project's when `projectId` is given.
 * Nothing is fetched for a blank query.
 */
export function useMemorySearch(query: string, projectId?: string) {
  const gateway = useService<IMemoryGatewayService>(MEMORY_GATEWAY_SERVICE_KEY);
  const trimmed = query.trim();
  return useQuery({
    queryKey: ['mimir', 'memory', 'search', projectId ?? '', trimmed],
    queryFn: () => gateway.search(trimmed, projectId ? { projectId } : {}),
    enabled: trimmed.length > 0,
  });
}

/** The changes waiting for the person: one layer's, or every layer they decide. */
export function useMemoryReview(layer?: string) {
  const gateway = useService<IMemoryGatewayService>(MEMORY_GATEWAY_SERVICE_KEY);
  return useQuery({
    queryKey: ['mimir', 'memory', 'review', layer ?? ''],
    queryFn: () => gateway.review(layer),
  });
}

/** The reason recorded when a person discards a change on the Review page. */
export const DISCARD_REASON = 'Discarded on the Review page';

/** Keep or discard one waiting change; the review is read again afterwards. */
export function useDecideProposal() {
  const gateway = useService<IMemoryGatewayService>(MEMORY_GATEWAY_SERVICE_KEY);
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ proposal, keep }: { proposal: MemoryProposal; keep: boolean }) =>
      keep ? gateway.keep(proposal) : gateway.discard(proposal, DISCARD_REASON),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mimir', 'memory', 'review'] }),
  });
}
