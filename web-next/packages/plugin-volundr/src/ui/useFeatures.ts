import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../ports/IVolundrService';

export function useFeatures(enabled = true, instanceId?: string) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: ['volundr', 'features', instanceId],
    queryFn: () => volundr.getFeatures(instanceId),
    enabled,
  });
}
