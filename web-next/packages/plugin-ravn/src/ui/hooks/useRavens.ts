import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IRavenStream } from '../../ports';

const RESIDENT_PROGRESS_POLL_MS = 2_000;

export function useRavens() {
  const service = useService<IRavenStream>('ravn.ravens');
  return useQuery({
    queryKey: ['ravn', 'ravens'],
    queryFn: () => service.listRavens(),
    refetchInterval: (query) =>
      query.state.data?.some((ravn) =>
        ['pending', 'deploying', 'deleting'].includes(ravn.observedState ?? ''),
      )
        ? RESIDENT_PROGRESS_POLL_MS
        : false,
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
