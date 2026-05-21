import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITingService } from '../ports';

export function usePhases(sagaId: string | null | undefined) {
  const ting = useService<ITingService>('ting');
  return useQuery({
    queryKey: ['ting', 'phases', sagaId],
    queryFn: () => ting.getPhases(sagaId!),
    enabled: !!sagaId,
  });
}
