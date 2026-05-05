import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRavenStream } from '../../ports';

export function useRavens() {
  const service = useService<IRavenStream>('ravn.ravens');
  return useQuery({
    queryKey: ['ravn', 'ravens'],
    queryFn: () => service.listRavens(),
  });
}

export function useRaven(id: string) {
  const service = useService<IRavenStream>('ravn.ravens');
  return useQuery({
    queryKey: ['ravn', 'ravens', id],
    queryFn: () => service.getRaven(id),
    enabled: !!id,
  });
}
