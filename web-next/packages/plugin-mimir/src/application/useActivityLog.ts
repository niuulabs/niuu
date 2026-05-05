import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

const DEFAULT_LIMIT = 50;

export function useActivityLog(limit = DEFAULT_LIMIT) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'activity-log', limit],
    queryFn: () => service.lint.getActivityLog(limit),
  });
}
