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

  it('covers huddle ownership, learning lifecycle, replay, and autonomy branches', async () => {
    const service = createMockValkyrieService();

    expect(await service.listEnvironments()).toHaveLength(3);
    expect(await service.getEnvironment('missing')).toBeNull();
    expect(await service.listFlocks()).toHaveLength(3);
    expect(await service.getFlock('missing')).toBeNull();
    await expect(service.getLearning('missing')).rejects.toThrow('Learning not found: missing');
    await expect(
      service.joinHuddle({
        huddleId: 'missing',
        participantId: 'human:jozef',
        action: 'observe',
      }),
    ).rejects.toThrow('Huddle not found: missing');
    await expect(
      service.joinHuddle({
        huddleId: 'huddle-valhalla-now',
        participantId: 'human:jozef',
        action: 'observe',
        targetFlockId: 'flock-personal',
      }),
    ).rejects.toThrow('Huddle belongs to flock-k8s');
    await expect(
      service.sendHuddleMessage({
        huddleId: 'huddle-valhalla-now',
        body: 'before join',
        authorId: 'human:jozef',
      }),
    ).rejects.toThrow('Join huddle before sending messages: huddle-valhalla-now');
    await expect(
      service.sendHuddleMessage({
        huddleId: 'missing',
        body: 'missing',
        authorId: 'human:jozef',
      }),
    ).rejects.toThrow('Huddle not found: missing');

    const joined = await service.joinHuddle({
      huddleId: 'huddle-valhalla-now',
      participantId: 'human:jozef',
      action: 'debug',
      targetFlockId: 'flock-k8s',
    });
    const joinedAgain = await service.joinHuddle({
      huddleId: 'huddle-valhalla-now',
      participantId: 'human:jozef',
      action: 'own',
      targetFlockId: 'flock-k8s',
    });
    const message = await service.sendHuddleMessage({
      huddleId: 'huddle-valhalla-now',
      body: 'same participant',
      authorId: 'human:jozef',
      directedTo: ['valkyrie-valhalla-sigrun'],
    });
    expect(joined.participantIds.filter((id) => id === 'human:jozef')).toHaveLength(1);
    expect(joinedAgain.joinedAction).toBe('own');
    expect(message.authorName).toBe('human:jozef');
    expect(message.directedTo).toEqual(['valkyrie-valhalla-sigrun']);

    const left = await service.leaveHuddle('huddle-valhalla-now');
    await expect(service.leaveHuddle('missing')).rejects.toThrow('Huddle not found: missing');

    expect(left.joinedParticipantId).toBeUndefined();

    expect(
      await service.rejectLearning({
        learningId: 'learn-k8s-eviction-rollback',
        reason: 'not useful',
        operatorId: 'human:jozef',
      }),
    ).toMatchObject({
      status: 'rejected',
      commandDelivery: { eventType: 'learning.adoption.recorded' },
    });
    expect(
      await service.overrideLearning({
        learningId: 'learn-email-vendor-escalation',
        reason: 'needed now',
      }),
    ).toMatchObject({ status: 'adopted', override: true });
    expect(
      await service.canaryLearning({
        learningId: 'learn-printer-resin-stall',
        canaryEnvironmentId: 'env-printer-forge',
      }),
    ).toMatchObject({
      status: 'canary',
      canaryEnvironmentId: 'env-printer-forge',
      commandDelivery: { eventType: 'learning.promoted' },
    });
    expect(await service.promoteLearning({ learningId: 'learn-k8s-oom-canary' })).toMatchObject({
      currentScope: 'flock',
    });
    expect(
      await service.demoteLearning({
        learningId: 'learn-k8s-oom-canary',
        targetScope: 'environment',
      }),
    ).toMatchObject({ currentScope: 'environment' });
    await expect(service.promoteLearning({ learningId: 'missing' })).rejects.toThrow(
      'Learning not found: missing',
    );
    await expect(service.demoteLearning({ learningId: 'missing' })).rejects.toThrow(
      'Learning not found: missing',
    );
    expect(
      await service.replaySignal({
        environmentId: 'env-k8s-valhalla',
        payload: { signal_id: 'sig-1', reason: 'OOMKilled' },
      }),
    ).toMatchObject({ decision: 'inspect_with_adopted_learning', usedAdoptedLearning: true });
    expect(await service.rollbackLearning({ learningId: 'learn-k8s-oom-canary' })).toMatchObject({
      status: 'rolled_back',
    });
    expect(
      await service.replaySignal({
        environmentId: 'env-k8s-valhalla',
        payload: { reason: 'unknown_capability' },
      }),
    ).toMatchObject({
      signalId: 'mock-signal',
      decision: 'defer_and_request_capability',
      usedAdoptedLearning: false,
    });
    expect(
      await service.updateAutonomy({
        valkyrieId: 'valkyrie-valhalla-sigrun',
        mode: 'manual',
        reason: 'pause',
      }),
    ).toMatchObject({
      valkyries: expect.arrayContaining([
        expect.objectContaining({ id: 'valkyrie-valhalla-sigrun', autonomyMode: 'manual' }),
      ]),
    });
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
