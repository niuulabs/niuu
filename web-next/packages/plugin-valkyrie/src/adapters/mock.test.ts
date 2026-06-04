import { describe, expect, it, vi } from 'vitest';
import { createMockValkyrieService, createMockValkyrieSignalStream } from './mock';

describe('createMockValkyrieService', () => {
  it('provides k8s, host, and printer environment scenarios', async () => {
    const dashboard = await createMockValkyrieService().getDashboard();

    expect(dashboard.environments.map((entry) => entry.kind)).toEqual([
      'kubernetes',
      'host',
      'printer',
    ]);
    expect(dashboard.flocks.map((entry) => entry.natsSubject)).toContain('flock.k8s.>');
    expect(dashboard.liveReport?.routeSubject).toBe('flock.k8s.>');
  });

  it('updates huddle membership and learning decisions', async () => {
    const service = createMockValkyrieService();
    const huddle = await service.joinHuddle('huddle-valhalla-now');
    const learning = await service.adoptLearning({
      learningId: 'learn-k8s-oom-canary',
      reason: 'test',
    });

    expect(huddle.joined).toBe(true);
    expect(learning.status).toBe('adopted');
  });
});

describe('createMockValkyrieSignalStream', () => {
  it('replays seed events and unsubscribes', () => {
    const stream = createMockValkyrieSignalStream();
    const listener = vi.fn();
    const unsubscribe = stream.subscribe(listener);
    unsubscribe();

    expect(listener).toHaveBeenCalled();
  });
});
