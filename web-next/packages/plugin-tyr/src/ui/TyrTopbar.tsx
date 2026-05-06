/**
 * TyrTopbar — dispatcher status chips rendered in the shell topbar-right area.
 *
 * Shown when the tyr plugin is active. Displays:
 *   - dispatcher on/off  (kind=ok/dim)
 *   - threshold value     (kind=dim)
 *   - concurrent X/Y      (kind=dim)
 *
 * When the user is on a /tyr/settings/* route, the settings breadcrumb
 * is shown instead (via SettingsTopbar).
 */

import { useRouterState } from '@tanstack/react-router';
import { TopbarChip } from '@niuulabs/ui';
import { useDispatcherState } from './useDispatcherState';
import { SettingsTopbar } from './settings/SettingsTopbar';

function DispatcherStats() {
  const { data: state } = useDispatcherState();

  if (!state) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" data-testid="tyr-topbar">
        <TopbarChip kind="dim" icon="◌" label="dispatcher …" testId="tyr-chip-dispatcher-…" />
      </div>
    );
  }

  const thresholdDisplay = (state.threshold / 100).toFixed(2);

  return (
    <div className="niuu-flex niuu-items-center niuu-gap-2" data-testid="tyr-topbar">
      <TopbarChip
        kind={state.running ? 'ok' : 'dim'}
        icon="●"
        label={`dispatcher ${state.running ? 'on' : 'off'}`}
        testId={`tyr-chip-dispatcher-${state.running ? 'on' : 'off'}`}
      />
      <TopbarChip
        kind="dim"
        icon="◈"
        label={`threshold ${thresholdDisplay}`}
        testId={`tyr-chip-threshold-${thresholdDisplay}`}
      />
      <TopbarChip
        kind="dim"
        icon="⇥"
        label={`concurrent ${state.maxConcurrentRaids}`}
        testId={`tyr-chip-concurrent-${state.maxConcurrentRaids}`}
      />
    </div>
  );
}

export function TyrTopbar() {
  const { location } = useRouterState({ select: (s) => ({ location: s.location }) });
  const pathname = location.pathname;

  // Settings routes show the breadcrumb instead
  if (pathname === '/tyr/settings' || pathname.startsWith('/tyr/settings/')) {
    return <SettingsTopbar />;
  }

  return <DispatcherStats />;
}
