import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITingService } from '../ports';

export function useSagas() {
  const ting = useService<ITingService>('ting');
  return useQuery({
    queryKey: ['ting', 'sagas'],
    queryFn: () => ting.getSagas(),
  });
}
