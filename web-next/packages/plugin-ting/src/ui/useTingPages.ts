import { usePluginEnabled } from '@niuulabs/shell';
import { useTingAvailable } from './useTingAvailable';

/**
 * Whether Ting's own pages can be opened here. A host may show another host's Ting on its
 * own pages with the Ting plugin off; then nothing may link to a `/ting` route.
 */
export function useTingPages(): boolean {
  const enabled = usePluginEnabled('ting');
  const available = useTingAvailable();
  return enabled && available;
}
