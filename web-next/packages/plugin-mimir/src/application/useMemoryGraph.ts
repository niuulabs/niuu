import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { KnowledgeGraph } from '../domain/api-types';

/** The knowledge graph of every memory layer the signed-in person can read (`GET /memory/graph`). */
export function useMemoryGraph() {
  const service = useService<IMimirService>('mimir');
  return useQuery<KnowledgeGraph>({
    queryKey: ['mimir', 'knowledge-graph'],
    queryFn: () => service.pages.getKnowledgeGraph(),
  });
}
