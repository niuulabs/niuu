import type { PipeCellStatus, DotState } from '@niuulabs/ui';
import type { PhaseStatus } from '../domain/saga';

export function phaseStatusToCell(status: PhaseStatus): PipeCellStatus {
  switch (status) {
    case 'complete':
      return 'ok';
    case 'active':
      return 'run';
    case 'gated':
      return 'gate';
    case 'pending':
      return 'pend';
  }
}

export function phaseStatusToStateDot(status: PhaseStatus): DotState {
  switch (status) {
    case 'complete':
      return 'merged';
    case 'active':
      return 'running';
    case 'gated':
      return 'attention';
    case 'pending':
      return 'idle';
  }
}
