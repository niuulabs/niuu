/**
 * RavnFooter — status chips rendered in the shell footer when Ravn is active.
 */

import { FooterChip, FooterChipSep } from '@niuulabs/shell';
import { useRavens } from './hooks/useRavens';
import { useSessions } from './hooks/useSessions';

export function RavnFooter() {
  const { data: ravens } = useRavens();
  const { data: sessions } = useSessions();

  const ravnList = ravens ?? [];
  const activeRavens = ravnList.filter((r) => r.status === 'active').length;
  const totalRavens = ravnList.length;
  const openSessions = (sessions ?? []).filter((s) => s.status === 'running').length;

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-1" data-testid="ravn-footer">
      <FooterChip
        name="ravens"
        state={activeRavens > 0 ? 'ok' : 'warn'}
        value={`${activeRavens}/${totalRavens}`}
      />
      <FooterChipSep />
      <FooterChip
        name="sessions"
        state={openSessions > 0 ? 'ok' : 'warn'}
        value={`${openSessions} active`}
      />
    </div>
  );
}
