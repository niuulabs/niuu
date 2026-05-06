import type { DotState } from '@niuulabs/ui';
import type { RavnState } from '../domain/ravn-binding';
import type { MountStatus } from '@niuulabs/domain';

export const RAVN_DOT_STATE: Record<RavnState, DotState> = {
  active: 'healthy',
  idle: 'idle',
  offline: 'failed',
};

export const MOUNT_DOT_STATE: Record<MountStatus, DotState> = {
  healthy: 'healthy',
  degraded: 'observing',
  down: 'failed',
};
