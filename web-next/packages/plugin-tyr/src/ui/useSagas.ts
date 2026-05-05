import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITyrService } from '../ports';

export function useSagas() {
  const tyr = useService<ITyrService>('tyr');
  return useQuery({
    queryKey: ['tyr', 'sagas'],
    queryFn: () => tyr.getSagas(),
  });
}
