/**
 * TyrFooter — status chips rendered in the shell footer when Tyr is active.
 *
 * Matches web2 pattern: api ● connected │ sleipnir ● 12.1k evt/s │ mímir ● idx 2.3M
 */

import { FooterChip, FooterChipSep } from '@niuulabs/shell';
import { useDispatcherState } from './useDispatcherState';

export function TyrFooter() {
  const { data: state } = useDispatcherState();

  const apiState = state ? 'ok' : 'warn';
  const apiLabel = state ? 'connected' : 'connecting';

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-1" data-testid="tyr-footer">
      <FooterChip name="api" state={apiState} value={apiLabel} />
      <FooterChipSep />
      <FooterChip
        name="dispatcher"
        state={state?.running ? 'ok' : 'warn'}
        value={state?.running ? 'active' : 'paused'}
      />
      <FooterChipSep />
      <FooterChip
        name="threshold"
        state="ok"
        value={state ? (state.threshold / 100).toFixed(2) : '—'}
      />
    </div>
  );
}
