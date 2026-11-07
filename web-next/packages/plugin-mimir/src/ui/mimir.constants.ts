import type { DotState } from '@niuulabs/ui';
import type { MountStatus } from '@niuulabs/domain';

export const MOUNT_DOT_STATE: Record<MountStatus, DotState> = {
  healthy: 'healthy',
  degraded: 'observing',
  down: 'failed',
};
