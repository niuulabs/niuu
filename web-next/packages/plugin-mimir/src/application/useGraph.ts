import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { nHopSubgraph } from './nHopSubgraph';
import type { IMimirService } from '../ports';
import type { MimirGraph } from '../domain/api-types';

const DEFAULT_HOPS = 2;

export interface UseGraphReturn {
  graph: MimirGraph | undefined;
  focusedGraph: MimirGraph | undefined;
  focusId: string | null;
  hops: number;
  setFocusId: (id: string | null) => void;
  setHops: (h: number) => void;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

export function useGraph(mountName?: string): UseGraphReturn {
  const [focusId, setFocusId] = useState<string | null>(null);
  const [hops, setHops] = useState(DEFAULT_HOPS);
  const service = useService<IMimirService>('mimir');

  const {
    data: graph,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['mimir', 'graph', mountName ?? null],
    queryFn: () => service.pages.getGraph(mountName ? { mountName } : undefined),
  });

  const focusedGraph = graph && focusId ? nHopSubgraph(graph, focusId, hops) : graph;

  return {
    graph,
    focusedGraph,
    focusId,
    hops,
    setFocusId,
    setHops,
    isLoading,
    isError,
    error,
  };
}
