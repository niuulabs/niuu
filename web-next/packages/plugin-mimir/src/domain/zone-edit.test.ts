import { describe, it, expect } from 'vitest';
import { zoneEditReducer } from './zone-edit';
import type { ZoneEditState, ZoneEditAction } from './zone-edit';
import type { Zone } from './page';

const ZONE_KEY_FACTS: Zone = { kind: 'key-facts', items: ['fact 1', 'fact 2'] };
const IDLE: ZoneEditState = { status: 'idle' };

function dispatch(state: ZoneEditState, action: ZoneEditAction): ZoneEditState {
  return zoneEditReducer(state, action);
}

describe('zoneEditReducer', () => {
  describe('START_EDIT', () => {
    it('transitions idle → editing', () => {
      const next = dispatch(IDLE, {
        type: 'START_EDIT',
        path: '/arch/overview',
        zoneKind: 'key-facts',
        zone: ZONE_KEY_FACTS,
      });
      expect(next.status).toBe('editing');
      if (next.status === 'editing') {
        expect(next.path).toBe('/arch/overview');
        expect(next.zoneKind).toBe('key-facts');
        expect(next.draft).toEqual(ZONE_KEY_FACTS);
      }
    });

    it('is a no-op when already editing', () => {
      const editing: ZoneEditState = {
        status: 'editing',
        path: '/arch/overview',
        zoneKind: 'key-facts',
        draft: ZONE_KEY_FACTS,
      };
      const next = dispatch(editing, {
        type: 'START_EDIT',
        path: '/other',
        zoneKind: 'assessment',
        zone: { kind: 'assessment', text: 'x' },
      });
      expect(next).toBe(editing);
    });
  });

  describe('UPDATE_DRAFT', () => {
    it('updates the draft while editing', () => {
      const editing: ZoneEditState = {
        status: 'editing',
        path: '/arch/overview',
        zoneKind: 'key-facts',
        draft: ZONE_KEY_FACTS,
      };
      const updated: Zone = { kind: 'key-facts', items: ['new fact'] };
      const next = dispatch(editing, { type: 'UPDATE_DRAFT', draft: updated });
      expect(next.status).toBe('editing');
      if (next.status === 'editing') expect(next.draft).toEqual(updated);
    });

    it('is a no-op when idle', () => {
      const next = dispatch(IDLE, {
        type: 'UPDATE_DRAFT',
        draft: { kind: 'key-facts', items: [] },
      });
      expect(next).toBe(IDLE);
    });
  });

  describe('BEGIN_SAVE', () => {
    it('transitions editing → saving', () => {
      const editing: ZoneEditState = {
        status: 'editing',
        path: '/arch/overview',
        zoneKind: 'key-facts',
        draft: ZONE_KEY_FACTS,
      };
      const next = dispatch(editing, {
        type: 'BEGIN_SAVE',
        destinationMounts: ['local', 'shared'],
      });
      expect(next.status).toBe('saving');
      if (next.status === 'saving') {
        expect(next.destinationMounts).toEqual(['local', 'shared']);
        expect(next.zone).toEqual(ZONE_KEY_FACTS);
      }
    });

    it('is a no-op when idle', () => {
      const next = dispatch(IDLE, { type: 'BEGIN_SAVE', destinationMounts: [] });
      expect(next).toBe(IDLE);
    });
  });

  describe('SAVE_SUCCESS', () => {
    it('transitions saving → saved', () => {
      const saving: ZoneEditState = {
        status: 'saving',
        path: '/arch/overview',
        zone: ZONE_KEY_FACTS,
        destinationMounts: ['local'],
      };
      const next = dispatch(saving, { type: 'SAVE_SUCCESS', savedAt: '2026-04-19T12:00:00Z' });
      expect(next.status).toBe('saved');
      if (next.status === 'saved') {
        expect(next.savedAt).toBe('2026-04-19T12:00:00Z');
        expect(next.destinationMounts).toEqual(['local']);
      }
    });

    it('is a no-op when not saving', () => {
      const next = dispatch(IDLE, { type: 'SAVE_SUCCESS', savedAt: '' });
      expect(next).toBe(IDLE);
    });
  });

  describe('SAVE_ERROR', () => {
    it('transitions saving → error', () => {
      const saving: ZoneEditState = {
        status: 'saving',
        path: '/arch/overview',
        zone: ZONE_KEY_FACTS,
        destinationMounts: ['local'],
      };
      const next = dispatch(saving, { type: 'SAVE_ERROR', message: 'conflict' });
      expect(next.status).toBe('error');
      if (next.status === 'error') expect(next.message).toBe('conflict');
    });
  });

  describe('CANCEL', () => {
    it('transitions editing → idle', () => {
      const editing: ZoneEditState = {
        status: 'editing',
        path: '/arch/overview',
        zoneKind: 'key-facts',
        draft: ZONE_KEY_FACTS,
      };
      expect(dispatch(editing, { type: 'CANCEL' }).status).toBe('idle');
    });

    it('transitions error → idle', () => {
      const error: ZoneEditState = { status: 'error', path: '/arch/overview', message: 'oops' };
      expect(dispatch(error, { type: 'CANCEL' }).status).toBe('idle');
    });

    it('is a no-op when idle', () => {
      const next = dispatch(IDLE, { type: 'CANCEL' });
      expect(next).toBe(IDLE);
    });
  });

  describe('RESET', () => {
    it('always returns idle', () => {
      const saving: ZoneEditState = {
        status: 'saving',
        path: '/arch/overview',
        zone: ZONE_KEY_FACTS,
        destinationMounts: [],
      };
      expect(dispatch(saving, { type: 'RESET' }).status).toBe('idle');
      expect(dispatch(IDLE, { type: 'RESET' }).status).toBe('idle');
    });
  });
});
