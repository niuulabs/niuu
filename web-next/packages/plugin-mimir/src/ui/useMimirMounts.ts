import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

export function useMimirMounts() {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'mounts'],
    queryFn: () => service.mounts.listMounts(),
  });
}
