import { beforeEach, describe, expect, it, vi } from 'vitest';
import { buildTingEventsHttpAdapter, createMockTingEvents } from './eventsHttp';

const openEventStream = vi.hoisted(() => vi.fn(() => ({ close: () => {} })));
vi.mock('@niuulabs/query', () => ({ openEventStream }));

const options = { onMessage: () => {} };

describe('Ting events', () => {
  beforeEach(() => openEventStream.mockClear());

  it.each(['/api/v1/ting', 'https://guild.example/api/v1/ting'])(
    "streams from the Ting it is given (%s), never the page's own origin by name",
    (basePath) => {
      buildTingEventsHttpAdapter({ basePath }).open(options);
      expect(openEventStream).toHaveBeenCalledExactlyOnceWith(`${basePath}/events`, options);
    },
  );

  it('stays quiet in demo data', () => {
    createMockTingEvents().open(options).close();
    expect(openEventStream).not.toHaveBeenCalled();
  });
});
