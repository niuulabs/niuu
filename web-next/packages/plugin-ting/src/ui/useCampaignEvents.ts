/**
 * useCampaignEvents — invalidate campaign queries when the runtime moves one.
 *
 * Ting streams `workflow.campaign.*` on its `/events`. Every surface
 * that lists campaigns wants the same reaction: invalidate its list so the
 * next paint is current. The subscription lives here so it exists once.
 *
 * Owner: plugin-ting.
 */

import { useEffect } from 'react';
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useTingEvents } from './useTingEvents';

const CAMPAIGN_EVENT_PREFIX = 'workflow.campaign.';

export function useCampaignEvents(queryKeys: QueryKey[]): void {
  const queryClient = useQueryClient();
  const events = useTingEvents();
  const serializedKeys = JSON.stringify(queryKeys);

  useEffect(() => {
    if (typeof window === 'undefined' || !events) return;
    const keys = JSON.parse(serializedKeys) as QueryKey[];
    const stream = events.open({
      onMessage: () => {},
      onEvent: ({ event }) => {
        if (!event?.startsWith(CAMPAIGN_EVENT_PREFIX)) return;
        for (const queryKey of keys) {
          void queryClient.invalidateQueries({ queryKey });
        }
      },
    });
    return () => stream.close();
  }, [events, queryClient, serializedKeys]);
}
