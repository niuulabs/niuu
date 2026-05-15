/**
 * TingTopbar — dispatcher status chips rendered in the shell topbar-right area.
 *
 * Shown when the ting plugin is active. Displays:
 *   - dispatcher on/off  (kind=ok/dim)
 *   - threshold value     (kind=dim)
 *   - concurrent X/Y      (kind=dim)
 *
 * When the user is on a /ting/settings/* route, the settings breadcrumb
 * is shown instead (via SettingsTopbar).
 */

import { useRouterState } from '@tanstack/react-router';
import { TopbarChip } from '@niuulabs/ui';
import { useDispatcherState } from './useDispatcherState';
import { SettingsTopbar } from './settings/SettingsTopbar';
import { formatThreshold } from './thresholdDisplay';

function DispatcherStats() {
  const { data: state } = useDispatcherState();

  if (!state) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" data-testid="ting-topbar">
        <TopbarChip kind="dim" icon="◌" label="dispatcher …" testId="ting-chip-dispatcher-…" />
      </div>
    );
  }

  const thresholdDisplay = formatThreshold(state.threshold);

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-2" data-testid="ting-topbar">
      <TopbarChip
        kind={state.running ? 'ok' : 'dim'}
        icon="●"
        label={`dispatcher ${state.running ? 'on' : 'off'}`}
        testId={`ting-chip-dispatcher-${state.running ? 'on' : 'off'}`}
      />
      <TopbarChip
        kind="dim"
        icon="◈"
        label={`threshold ${thresholdDisplay}`}
        testId={`ting-chip-threshold-${thresholdDisplay}`}
      />
      <TopbarChip
        kind="dim"
        icon="⇥"
        label={`concurrent ${state.maxConcurrentRuns}`}
        testId={`ting-chip-concurrent-${state.maxConcurrentRuns}`}
      />
    </div>
  );
}

export function TingTopbar() {
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const pathname = location.pathname;

  // Settings routes show the breadcrumb instead
  if (pathname === '/ting/settings' || pathname.startsWith('/ting/settings/')) {
    return <SettingsTopbar />;
  }

  return <DispatcherStats />;
}
