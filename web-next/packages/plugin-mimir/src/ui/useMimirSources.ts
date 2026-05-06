import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { OriginType } from '../domain/source';

export function useMimirSources(options?: { originType?: OriginType; mountName?: string }) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'sources', options],
    queryFn: () => service.pages.listSources(options),
  });
}

export function useMimirRecentWrites(limit = 20) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'recent-writes', limit],
    queryFn: () => service.mounts.getRecentWrites(limit),
  });
}
