import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ServicesProvider, UNAVAILABLE_SERVICE } from '@niuulabs/plugin-sdk';
import { useCampaignEvents } from './useCampaignEvents';

const openEvents = vi.fn();

const KEYS = [
  ['ting', 'specs', 'campaigns'],
  ['ting', 'research', 'campaigns'],
];

function Probe() {
  useCampaignEvents(KEYS);
  return null;
}

describe('useCampaignEvents', () => {
  beforeEach(() => {
    openEvents.mockReset();
  });

  it('invalidates every key on a campaign event and ignores the rest', () => {
    const close = vi.fn();
    openEvents.mockReturnValue({ close });
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    const { unmount } = render(
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'ting.events': { open: openEvents } }}>
          <Probe />
        </ServicesProvider>
      </QueryClientProvider>,
    );

    const options = openEvents.mock.calls[0]?.[0] as {
      onMessage: (payload: unknown) => void;
      onEvent: (payload: { event?: string; data: string }) => void;
    };

    // A message with no event name is not a campaign move.
    options.onMessage({ data: '{}' });
    options.onEvent({ data: '{}' });
    options.onEvent({ event: 'saga.updated', data: '{}' });
    expect(invalidate).not.toHaveBeenCalled();

    options.onEvent({ event: 'workflow.campaign.updated', data: '{}' });
    expect(invalidate).toHaveBeenCalledTimes(KEYS.length);

    unmount();
    expect(close).toHaveBeenCalled();
  });

  it('opens nothing where this host runs no Ting', () => {
    const absent = {
      [UNAVAILABLE_SERVICE]: { available: false, serviceName: 'ting.events', reason: 'off' },
      open: openEvents,
    };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ServicesProvider services={{ 'ting.events': absent }}>
          <Probe />
        </ServicesProvider>
      </QueryClientProvider>,
    );
    expect(openEvents).not.toHaveBeenCalled();
  });
});
