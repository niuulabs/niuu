import { useEffect, useRef, useState } from 'react';
import { TopbarChip } from '@niuulabs/ui';
import { useDispatcherState } from './useDispatcherState';
import { formatThreshold } from './thresholdDisplay';

export function TingTopbar() {
  const { data: state } = useDispatcherState();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const status = state ? (state.running ? 'active' : 'paused') : 'loading';

  useEffect(() => {
    if (!open) return;
    function dismiss(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  const thresholdDisplay = state ? formatThreshold(state.threshold) : '—';
  const concurrentDisplay = state ? String(state.maxConcurrentRuns) : '—';

  return (
    <div className="niuu:relative" data-testid="ting-topbar" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="niuu:block niuu:rounded-md niuu:border-0 niuu:bg-transparent niuu:p-0 niuu:focus-visible:outline-2 niuu:focus-visible:outline-offset-2 niuu:focus-visible:outline-brand"
        aria-label={`Dispatcher status: ${status}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="ting-dispatcher-status"
        onClick={() => setOpen((value) => !value)}
      >
        <TopbarChip
          kind={state?.running ? 'ok' : 'dim'}
          icon={state ? '●' : '◌'}
          label={`dispatcher ${status}`}
          testId={`ting-chip-dispatcher-${status}`}
        />
      </button>
      <div
        id="ting-dispatcher-status"
        role="dialog"
        aria-label="Dispatcher status"
        hidden={!open}
        className="niuu:absolute niuu:right-0 niuu:top-[calc(100%+8px)] niuu:z-50 niuu:w-[240px] niuu:space-y-3 niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-3 niuu:shadow-lg"
      >
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
          <strong className="niuu:text-sm niuu:text-text-primary">Dispatcher</strong>
          <span className="niuu:text-xs niuu:text-text-muted">{status}</span>
        </div>
        <dl className="niuu:grid niuu:grid-cols-[1fr_auto] niuu:gap-x-4 niuu:gap-y-2 niuu:text-xs">
          <dt className="niuu:text-text-muted">Threshold</dt>
          <dd className="niuu:font-mono niuu:text-text-secondary">{thresholdDisplay}</dd>
          <dt className="niuu:text-text-muted">Concurrent runs</dt>
          <dd className="niuu:font-mono niuu:text-text-secondary">{concurrentDisplay}</dd>
        </dl>
      </div>
    </div>
  );
}
