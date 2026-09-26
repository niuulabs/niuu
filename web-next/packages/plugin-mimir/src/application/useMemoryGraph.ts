import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { MimirGraph } from '../domain/api-types';

/** The knowledge graph for the Memory Explore view, optionally scoped to one mount. */
export function useMemoryGraph(mountName?: string) {
  const service = useService<IMimirService>('mimir');
  return useQuery<MimirGraph>({
    queryKey: ['mimir', 'memory-graph', mountName ?? null],
    queryFn: () => service.pages.getGraph(mountName ? { mountName } : undefined),
  });
}
