import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../ports/IVolundrService';

/** Queries the active sessions list via IVolundrService. */
export function useVolundrSessions() {
  const service = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: ['volundr', 'sessions'],
    queryFn: () => service.getSessions(),
  });
}

/** Queries summary stats from IVolundrService. */
export function useVolundrStats() {
  const service = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: ['volundr', 'stats'],
    queryFn: () => service.getStats(),
  });
}
