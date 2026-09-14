import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useCampaignEvents } from './useCampaignEvents';

const mockOpenEventStream = vi.hoisted(() => vi.fn());

vi.mock('@niuulabs/query', () => ({
  openEventStream: mockOpenEventStream,
}));

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
    mockOpenEventStream.mockReset();
  });

  it('invalidates every key on a campaign event and ignores the rest', () => {
    const close = vi.fn();
    mockOpenEventStream.mockReturnValue({ close });
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    const { unmount } = render(
      <QueryClientProvider client={client}>
        <Probe />
      </QueryClientProvider>,
    );

    const options = mockOpenEventStream.mock.calls[0]?.[1] as {
      onMessage: (payload: unknown) => void;
      onEvent: (payload: { event?: string; data: string }) => void;
    };
    expect(mockOpenEventStream.mock.calls[0]?.[0]).toBe('/api/v1/ting/events');

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
});
