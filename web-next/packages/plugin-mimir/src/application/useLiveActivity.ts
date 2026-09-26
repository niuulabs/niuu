import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

/** Poll interval for the "Right now" live-activity feed and scene markers. */
export const LIVE_ACTIVITY_POLL_MS = 5_000;

/** Live "who's touching memory right now" feed, polled on an interval. */
export function useLiveActivity() {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'live-activity'],
    queryFn: () => service.pages.getLiveActivity(),
    refetchInterval: LIVE_ACTIVITY_POLL_MS,
  });
}
