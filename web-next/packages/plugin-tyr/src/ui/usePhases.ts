import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITyrService } from '../ports';

export function usePhases(sagaId: string | null | undefined) {
  const tyr = useService<ITyrService>('tyr');
  return useQuery({
    queryKey: ['tyr', 'phases', sagaId],
    queryFn: () => tyr.getPhases(sagaId!),
    enabled: !!sagaId,
  });
}
