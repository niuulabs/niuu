import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IDispatcherService } from '../ports';

export function useDispatcherState() {
  const dispatcher = useService<IDispatcherService>('tyr.dispatcher');
  return useQuery({
    queryKey: ['tyr', 'dispatcher'],
    queryFn: () => dispatcher.getState(),
  });
}
