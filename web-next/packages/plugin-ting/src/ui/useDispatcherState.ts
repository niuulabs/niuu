import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IDispatcherService } from '../ports';

export function useDispatcherState() {
  const dispatcher = useService<IDispatcherService>('ting.dispatcher');
  return useQuery({
    queryKey: ['ting', 'dispatcher'],
    queryFn: () => dispatcher.getState(),
  });
}
