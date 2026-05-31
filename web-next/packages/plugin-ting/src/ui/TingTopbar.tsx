import { TopbarChip } from '@niuulabs/ui';
import { useDispatcherState } from './useDispatcherState';
import { formatThreshold } from './thresholdDisplay';

function DispatcherStats() {
  const { data: state } = useDispatcherState();

  if (!state) {
    return (
      <div className="niuu:flex niuu:items-center niuu:gap-2" data-testid="ting-topbar">
        <TopbarChip kind="dim" icon="◌" label="dispatcher …" testId="ting-chip-dispatcher-…" />
      </div>
    );
  }

  const thresholdDisplay = formatThreshold(state.threshold);

  return (
    <div className="niuu:flex niuu:items-center niuu:gap-2" data-testid="ting-topbar">
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
  return <DispatcherStats />;
}
