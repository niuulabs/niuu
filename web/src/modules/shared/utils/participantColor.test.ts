import { describe, it, expect } from 'vitest';
import {
  resolveParticipantColor,
  PARTICIPANT_COLOR_MAP,
  PARTICIPANT_SLOT_COUNT,
  participantSlot,
} from './participantColor';

describe('resolveParticipantColor', () => {
  it('resolves brand-derived slot names to CSS vars', () => {
    expect(resolveParticipantColor('p1')).toBe('var(--color-participant-1)');
    expect(resolveParticipantColor('p2')).toBe('var(--color-participant-2)');
    expect(resolveParticipantColor('p3')).toBe('var(--color-participant-3)');
    expect(resolveParticipantColor('p4')).toBe('var(--color-participant-4)');
    expect(resolveParticipantColor('p5')).toBe('var(--color-participant-5)');
    expect(resolveParticipantColor('p6')).toBe('var(--color-participant-6)');
    expect(resolveParticipantColor('p7')).toBe('var(--color-participant-7)');
  });

  it('falls back to secondary text color for unknown colors', () => {
    expect(resolveParticipantColor('unknown')).toBe('var(--color-text-secondary)');
    expect(resolveParticipantColor('')).toBe('var(--color-text-secondary)');
  });

  it('falls back for legacy named colors no longer in the map', () => {
    expect(resolveParticipantColor('amber')).toBe('var(--color-text-secondary)');
    expect(resolveParticipantColor('cyan')).toBe('var(--color-text-secondary)');
  });

  it('PARTICIPANT_COLOR_MAP contains all expected slot keys', () => {
    const keys = Object.keys(PARTICIPANT_COLOR_MAP);
    expect(keys).toHaveLength(PARTICIPANT_SLOT_COUNT);
    for (let i = 1; i <= PARTICIPANT_SLOT_COUNT; i++) {
      expect(keys).toContain(`p${i}`);
    }
  });
});

describe('PARTICIPANT_SLOT_COUNT', () => {
  it('equals the number of entries in the color map', () => {
    expect(PARTICIPANT_SLOT_COUNT).toBe(Object.keys(PARTICIPANT_COLOR_MAP).length);
  });
});

describe('participantSlot', () => {
  it('returns p1 for index 0', () => {
    expect(participantSlot(0)).toBe('p1');
  });

  it('returns correct slot for indices within range', () => {
    expect(participantSlot(0)).toBe('p1');
    expect(participantSlot(1)).toBe('p2');
    expect(participantSlot(6)).toBe('p7');
  });

  it('wraps around for indices beyond slot count', () => {
    expect(participantSlot(7)).toBe('p1');
    expect(participantSlot(8)).toBe('p2');
    expect(participantSlot(14)).toBe('p1');
  });
});
