import { openEventStream, type ApiClient } from '@niuulabs/query';
import type { ITingEvents } from '../ports';

/** Ting's event stream, on the host `client` is rooted at (`<ting>/events`). */
export function buildTingEventsHttpAdapter(client: Pick<ApiClient, 'basePath'>): ITingEvents {
  return {
    open: (options) => openEventStream(`${client.basePath}/events`, options),
  };
}

/** Demo data never changes on its own, so its stream stays quiet. */
export function createMockTingEvents(): ITingEvents {
  return { open: () => ({ close: () => {} }) };
}
