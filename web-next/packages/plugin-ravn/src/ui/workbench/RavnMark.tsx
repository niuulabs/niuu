import { cn } from '@niuulabs/ui';
import type { Ravn } from '../../domain/ravn';
import { nameForRavn } from '../../domain/residentActions';
import {
  RAVN_STATE_LABEL,
  ravnLifeState,
  type RavnLifeState,
} from '../../application/ravnWorkbench';

type MarkSize = 'sm' | 'md' | 'lg';

/** A ravn's mark: its initial, outlined in its engine's colour, with a state pip. */
export function RavnMark({
  ravn,
  size = 'md',
  showState = false,
}: {
  ravn: Ravn;
  size?: MarkSize;
  showState?: boolean;
}) {
  const state = ravnLifeState(ravn);
  const letter = (ravn.letter || nameForRavn(ravn)).charAt(0).toUpperCase();
  return (
    <span
      className={cn('rw-mark', size !== 'md' && `rw-mark--${size}`)}
      data-engine={ravn.engine ?? 'persona'}
      aria-hidden="true"
    >
      {letter}
      {showState && <span className="rw-mark__pip" data-state={state} />}
    </span>
  );
}

export function RavnStateBadge({ state }: { state: RavnLifeState }) {
  return (
    <span className="rw-state" data-state={state} data-testid="ravn-state">
      {RAVN_STATE_LABEL[state]}
    </span>
  );
}

export function EngineLabel({ engine }: { engine: Ravn['engine'] }) {
  return (
    <span className="rw-engine" data-engine={engine ?? 'persona'}>
      {engine ?? 'persona'}
    </span>
  );
}
