import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IClusterAdapter } from '../ports/IClusterAdapter';

/** Queries all clusters via the cluster adapter. */
export function useClusters() {
  const adapter = useService<IClusterAdapter>('volundr.clusters');
  return useQuery({
    queryKey: ['volundr', 'clusters'],
    queryFn: () => adapter.getClusters(),
  });
}
