import { describe, expect, it } from 'vitest';
import { normalizeValkyrieSignalEvent } from './domain';

describe('normalizeValkyrieSignalEvent', () => {
  it('normalizes a signal event from wire data', () => {
    expect(
      normalizeValkyrieSignalEvent({
        id: 'event-1',
        type: 'learning',
        environmentId: 'env-k8s-valhalla',
        flockId: 'flock-k8s',
        message: 'Peer learning entered canary',
        severity: 'warning',
        timestamp: '2026-06-03T14:00:00Z',
      }),
    ).toEqual({
      id: 'event-1',
      type: 'learning',
      environmentId: 'env-k8s-valhalla',
      flockId: 'flock-k8s',
      summary: 'Peer learning entered canary',
      severity: 'warning',
      timestamp: '2026-06-03T14:00:00Z',
    });
  });

  it('rejects empty payloads without a summary', () => {
    expect(normalizeValkyrieSignalEvent({ id: 'event-2' })).toBeNull();
  });

  it('defaults unknown type and severity safely', () => {
    expect(
      normalizeValkyrieSignalEvent({
        type: 'unknown',
        severity: 'loud',
        summary: 'fallback event',
      }),
    ).toMatchObject({
      type: 'signal',
      severity: 'info',
      summary: 'fallback event',
    });
  });
});
