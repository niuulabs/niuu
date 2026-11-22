import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITingService } from '../ports';

/** `enabled: false` asks Ting nothing, for a host that runs none. */
export function useSagas({ enabled = true }: { enabled?: boolean } = {}) {
  const ting = useService<ITingService>('ting');
  return useQuery({
    queryKey: ['ting', 'sagas'],
    queryFn: () => ting.getSagas(),
    enabled,
  });
}
