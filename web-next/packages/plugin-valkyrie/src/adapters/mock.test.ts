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
    const huddle = await service.joinHuddle({
      huddleId: 'huddle-valhalla-now',
      participantId: 'human:jozef',
      displayName: 'Jozef',
      action: 'teach',
      targetFlockId: 'flock-k8s',
    });
    const message = await service.sendHuddleMessage({
      huddleId: 'huddle-valhalla-now',
      body: 'Teach this.',
      authorId: 'human:jozef',
    });
    const learning = await service.adoptLearning({
      learningId: 'learn-k8s-oom-canary',
      reason: 'test',
    });

    expect(huddle.joined).toBe(true);
    expect(huddle.joinedParticipantId).toBe('human:jozef');
    expect(huddle.joinedAction).toBe('teach');
    expect(message.authorId).toBe('human:jozef');
    expect(message.authorName).toBe('Jozef');
    expect(learning.status).toBe('adopted');
  });

  it('rejects huddle messages that do not match the joined participant', async () => {
    const service = createMockValkyrieService();
    await service.joinHuddle({
      huddleId: 'huddle-valhalla-now',
      participantId: 'human:jozef',
      action: 'teach',
      targetFlockId: 'flock-k8s',
    });

    await expect(
      service.sendHuddleMessage({
        huddleId: 'huddle-valhalla-now',
        body: 'Nope.',
        authorId: 'operator',
      }),
    ).rejects.toThrow('Huddle is joined as human:jozef, not operator');
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
