import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

const DEFAULT_LIMIT = 20;

export function useDreams(limit = DEFAULT_LIMIT) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'dreams', limit],
    queryFn: () => service.lint.getDreamCycles(limit),
  });
}
