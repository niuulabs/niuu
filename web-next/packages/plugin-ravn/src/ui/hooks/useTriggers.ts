import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ITriggerStore } from '../../ports';

export function useTriggers() {
  const service = useService<ITriggerStore>('ravn.triggers');
  return useQuery({
    queryKey: ['ravn', 'triggers'],
    queryFn: () => service.listTriggers(),
  });
}
