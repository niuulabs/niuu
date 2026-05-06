/**
 * Zone edit state machine.
 *
 * Tracks the lifecycle of an in-place zone edit:
 *   idle → editing → saving → saved | error
 *              ↑                 ↓
 *            cancel            reset
 *
 * "Optimistic locking" is handled by recording the destination mounts at the
 * moment the save is dispatched. The UI shows which mount(s) received the
 * write so the operator can confirm the routing decision.
 */

import type { Zone, ZoneKind } from './page';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export type ZoneEditState =
  | { status: 'idle' }
  | { status: 'editing'; path: string; zoneKind: ZoneKind; draft: Zone }
  | {
      status: 'saving';
      path: string;
      zone: Zone;
      /** Mounts that will receive the write (resolved from routing rules). */
      destinationMounts: string[];
    }
  | {
      status: 'saved';
      path: string;
      destinationMounts: string[];
      savedAt: string;
    }
  | { status: 'error'; path: string; message: string };

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export type ZoneEditAction =
  | { type: 'START_EDIT'; path: string; zoneKind: ZoneKind; zone: Zone }
  | { type: 'UPDATE_DRAFT'; draft: Zone }
  | { type: 'BEGIN_SAVE'; destinationMounts: string[] }
  | { type: 'SAVE_SUCCESS'; savedAt: string }
  | { type: 'SAVE_ERROR'; message: string }
  | { type: 'CANCEL' }
  | { type: 'RESET' };

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

export function zoneEditReducer(state: ZoneEditState, action: ZoneEditAction): ZoneEditState {
  switch (action.type) {
    case 'START_EDIT': {
      if (state.status !== 'idle') return state;
      return {
        status: 'editing',
        path: action.path,
        zoneKind: action.zoneKind,
        draft: action.zone,
      };
    }

    case 'UPDATE_DRAFT': {
      if (state.status !== 'editing') return state;
      return { ...state, draft: action.draft };
    }

    case 'BEGIN_SAVE': {
      if (state.status !== 'editing') return state;
      return {
        status: 'saving',
        path: state.path,
        zone: state.draft,
        destinationMounts: action.destinationMounts,
      };
    }

    case 'SAVE_SUCCESS': {
      if (state.status !== 'saving') return state;
      return {
        status: 'saved',
        path: state.path,
        destinationMounts: state.destinationMounts,
        savedAt: action.savedAt,
      };
    }

    case 'SAVE_ERROR': {
      if (state.status !== 'saving') return state;
      return { status: 'error', path: state.path, message: action.message };
    }

    case 'CANCEL': {
      if (state.status !== 'editing' && state.status !== 'error') return state;
      return { status: 'idle' };
    }

    case 'RESET':
      return { status: 'idle' };
  }
}
