/**
 * useCampaignEvents — invalidate campaign queries when the runtime moves one.
 *
 * Ting streams `workflow.campaign.*` on `/api/v1/ting/events`. Every surface
 * that lists campaigns wants the same reaction: invalidate its list so the
 * next paint is current. The subscription lives here so it exists once.
 *
 * Owner: plugin-ting.
 */

import { useEffect } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { openEventStream } from '@niuulabs/query';

const CAMPAIGN_EVENT_PREFIX = 'workflow.campaign.';

export function useCampaignEvents(queryKeys: QueryKey[]): void {
  const queryClient = useQueryClient();
  const serializedKeys = JSON.stringify(queryKeys);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const keys = JSON.parse(serializedKeys) as QueryKey[];
    const stream = openEventStream('/api/v1/ting/events', {
      onMessage: () => {},
      onEvent: ({ event }) => {
        if (!event?.startsWith(CAMPAIGN_EVENT_PREFIX)) return;
        for (const queryKey of keys) {
          void queryClient.invalidateQueries({ queryKey });
        }
      },
    });
    return () => stream.close();
  }, [queryClient, serializedKeys]);
}
