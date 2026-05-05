import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IClusterAdapter } from '../../ports/IClusterAdapter';

/** Queries all clusters via IClusterAdapter. */
export function useVolundrClusters() {
  const adapter = useService<IClusterAdapter>('clusterAdapter');
  return useQuery({
    queryKey: ['volundr', 'clusters'],
    queryFn: () => adapter.getClusters(),
  });
}
