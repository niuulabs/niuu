import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

/** Latest retrieval-eval report — null when the backend has none. */
export function useEvalReport() {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'eval', 'latest'],
    queryFn: () => service.mounts.getEvalReport(),
  });
}

/** Live query-traffic stats — null when the backend has none. */
export function useQueryStats() {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'eval', 'queries'],
    queryFn: () => service.mounts.getQueryStats(),
  });
}
