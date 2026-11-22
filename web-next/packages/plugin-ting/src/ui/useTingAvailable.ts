import { useServiceAvailable } from '@niuulabs/plugin-sdk';

/**
 * Whether this host runs Ting. Pages outside Ting leave out what only Ting knows when it
 * does not. With any part of Ting configured it does, and a part that fails shows its error.
 */
export function useTingAvailable(): boolean {
  const ting = useServiceAvailable('ting');
  const work = useServiceAvailable('ting.work');
  return ting || work;
}
