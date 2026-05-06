import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

export function useRavns() {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'ravns'],
    queryFn: () => service.mounts.listRavnBindings(),
  });
}
