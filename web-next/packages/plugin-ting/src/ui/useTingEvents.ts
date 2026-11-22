import { isUnavailableService, useService } from '@niuulabs/plugin-sdk';
import type { ITingEvents } from '../ports';

/** Ting's event stream, or null where this host runs no Ting and none can be opened. */
export function useTingEvents(): ITingEvents | null {
  const events = useService<ITingEvents>('ting.events');
  return isUnavailableService(events) ? null : events;
}
