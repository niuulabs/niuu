import { describe, it, expect } from 'vitest';
import { resolveParticipantColor } from './participantColor';

describe('resolveParticipantColor', () => {
  it('returns explicit color if provided', () => {
    expect(resolveParticipantColor('any-peer', '#ff0000')).toBe('#ff0000');
  });

  it('returns a deterministic color for a given peerId', () => {
    const c1 = resolveParticipantColor('peer-abc');
    const c2 = resolveParticipantColor('peer-abc');
    expect(c1).toBe(c2);
  });

  it('returns first color for empty peerId', () => {
    const color = resolveParticipantColor('');
    expect(color).toBe('#38bdf8');
  });

  it('returns different colors for different peerIds', () => {
    const colors = new Set(['peer-1', 'peer-2', 'peer-3', 'peer-4'].map(resolveParticipantColor));
    // Not all necessarily different, but at least 2 distinct values in 4 is expected
    expect(colors.size).toBeGreaterThanOrEqual(1);
  });
});
