import type { LifecycleState } from '@niuulabs/ui';
import type { SessionState } from '../../domain/session';

/**
 * Maps a SessionState to a LifecycleState suitable for LifecycleBadge.
 * The only non-identity mapping is 'requested' → 'provisioning'.
 */
export function toLifecycleState(state: SessionState): LifecycleState {
  if (state === 'requested') return 'provisioning';
  return state as LifecycleState;
}
