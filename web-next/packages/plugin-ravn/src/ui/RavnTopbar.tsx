/**
 * RavnTopbar — stat chips rendered in the shell topbar right section.
 *
 * Shown when the ravn plugin is active (shell calls topbarRight only for
 * the active plugin, so no route check is needed).
 *
 * Chips:
 *   - {activeRavens} active  (kind=ok, dot=●)
 *   - {failedRavens} failed  (kind=err, conditional)
 *   - {openSessions} sessions (kind=dim, icon=◷)
 */

import { TopbarChip } from '@niuulabs/ui';
import { useRavens } from './hooks/useRavens';
import { useSessions } from './hooks/useSessions';

export function RavnTopbar() {
  const { data: ravens } = useRavens();
  const { data: sessions } = useSessions();

  const ravnList = ravens ?? [];
  const activeRavens = ravnList.filter((r) => r.status === 'active').length;
  const failedRavens = ravnList.filter((r) => r.status === 'failed').length;
  const openSessions = (sessions ?? []).filter((s) => s.status === 'running').length;

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-2" data-testid="ravn-topbar">
      <TopbarChip kind="ok" icon="●" label={`${activeRavens} active`} />
      {failedRavens > 0 && <TopbarChip kind="err" icon="●" label={`${failedRavens} failed`} />}
      <TopbarChip kind="dim" icon="◷" label={`${openSessions} sessions`} />
    </div>
  );
}
