import { describe, expect, it } from 'vitest';
import {
  advanceDelivery,
  deliveryFromSteeringState,
  parseMessageAuthor,
  parseAgentUpdate,
  parseDeliveryFrame,
  parseEffortFrame,
  parsePlanFrame,
  parseRuntimeOptionsFrame,
  parseUsageFrame,
  upsertAgent,
} from './sessionFrames';
import type { SessionAgent } from '../types';

describe('parsePlanFrame', () => {
  it('keeps well-formed tasks and counts them by status', () => {
    expect(
      parsePlanFrame({
        type: 'plan',
        tasks: [
          { content: 'Read the code', status: 'completed' },
          { content: 'Write the fix', status: 'in_progress', activeForm: 'Writing the fix' },
          { content: 'Run the tests', status: 'pending' },
          { content: '', status: 'pending' },
          { status: 'pending' },
          'not-a-task',
        ],
      }),
    ).toEqual({
      tasks: [
        { content: 'Read the code', status: 'completed' },
        { content: 'Write the fix', status: 'in_progress', activeForm: 'Writing the fix' },
        { content: 'Run the tests', status: 'pending' },
      ],
      counts: { total: 3, pending: 1, in_progress: 1, completed: 1 },
    });
  });

  it('accepts an empty plan and rejects a frame without a task list', () => {
    expect(parsePlanFrame({ tasks: [] })?.counts.total).toBe(0);
    expect(parsePlanFrame({ tasks: 'nope' })).toBeNull();
    expect(parsePlanFrame({})).toBeNull();
  });
});

describe('parseAgentUpdate / upsertAgent', () => {
  const started = parseAgentUpdate({
    action: 'started',
    agent: {
      id: 'a1',
      kind: 'subagent',
      name: 'explorer',
      status: 'running',
      description: 'Look around',
      model: 'opus',
      started_at: '2026-11-05T10:00:00Z',
    },
  })!;

  it('maps the wire fields to a session agent', () => {
    expect(started.action).toBe('started');
    expect(upsertAgent([], started)).toEqual([
      {
        id: 'a1',
        kind: 'subagent',
        name: 'explorer',
        status: 'running',
        description: 'Look around',
        model: 'opus',
        startedAt: '2026-11-05T10:00:00Z',
      },
    ]);
  });

  it('keeps a finished agent with its status and end time', () => {
    const stopped = parseAgentUpdate({
      action: 'stopped',
      agent: { id: 'a1', status: 'failed', ended_at: '2026-11-05T10:01:00Z' },
    })!;
    expect(upsertAgent(upsertAgent([], started), stopped)).toEqual([
      expect.objectContaining({
        id: 'a1',
        name: 'explorer',
        status: 'failed',
        endedAt: '2026-11-05T10:01:00Z',
        startedAt: '2026-11-05T10:00:00Z',
      }),
    ]);
  });

  it('reads a stopped frame without a status as done, and a started one as running', () => {
    expect(parseAgentUpdate({ action: 'stopped', agent: { id: 'x' } })?.agent.status).toBe('done');
    expect(parseAgentUpdate({ action: 'started', agent: { id: 'x' } })?.agent.status).toBe(
      'running',
    );
  });

  it('does not invent an agent it cannot describe', () => {
    const unknown = parseAgentUpdate({ action: 'stopped', agent: { id: 'ghost' } })!;
    const existing: SessionAgent[] = [];
    expect(upsertAgent(existing, unknown)).toBe(existing);
  });

  it('rejects malformed frames', () => {
    expect(parseAgentUpdate({ action: 'paused', agent: { id: 'a' } })).toBeNull();
    expect(parseAgentUpdate({ action: 'started', agent: 'a' })).toBeNull();
    expect(parseAgentUpdate({ action: 'started', agent: { name: 'no id' } })).toBeNull();
  });
});

describe('parseEffortFrame', () => {
  it('reads the levels and whether they can change', () => {
    expect(
      parseEffortFrame({
        current: 'high',
        levels: ['low', 'high', 7],
        mutable: true,
        applies_to: 'next_turn',
      }),
    ).toEqual({ current: 'high', levels: ['low', 'high'], mutable: true, appliesTo: 'next_turn' });
  });

  it('reports no current effort as null and rejects an incomplete frame', () => {
    expect(parseEffortFrame({ current: null, levels: [], mutable: false })?.current).toBeNull();
    expect(parseEffortFrame({ levels: [] })).toBeNull();
    expect(parseEffortFrame({ mutable: true })).toBeNull();
  });
});

describe('parseRuntimeOptionsFrame', () => {
  it('reads the catalog and the current choice, dropping malformed models', () => {
    expect(
      parseRuntimeOptionsFrame({
        applies_to: 'next_turn',
        models: [
          {
            id: 'gpt-5',
            model: 'gpt-5',
            name: 'GPT-5',
            description: 'Frontier',
            default: true,
            hidden: false,
            effort_levels: ['low', 'high'],
            default_effort: 'high',
          },
          { id: 'old', model: 'old', name: 'Old', hidden: true },
          { id: 'broken' },
          null,
        ],
        current: { model: 'gpt-5', effort: 'high', service_tier: null },
      }),
    ).toEqual({
      appliesTo: 'next_turn',
      current: { model: 'gpt-5', effort: 'high' },
      models: [
        {
          id: 'gpt-5',
          model: 'gpt-5',
          name: 'GPT-5',
          description: 'Frontier',
          isDefault: true,
          hidden: false,
          effortLevels: ['low', 'high'],
          defaultEffort: 'high',
        },
        { id: 'old', model: 'old', name: 'Old', isDefault: false, hidden: true, effortLevels: [] },
      ],
    });
  });

  it('rejects a frame without a catalog or a current model', () => {
    expect(parseRuntimeOptionsFrame({ models: [], current: {} })).toBeNull();
    expect(parseRuntimeOptionsFrame({ current: { model: 'm' } })).toBeNull();
    expect(parseRuntimeOptionsFrame({ models: [] })).toBeNull();
  });

  it('keeps the current effort null when the runtime reports none', () => {
    expect(parseRuntimeOptionsFrame({ models: [], current: { model: 'm' } })?.current).toEqual({
      model: 'm',
      effort: null,
    });
  });
});

describe('parseUsageFrame', () => {
  it('sums fresh and cached input into the context and totals', () => {
    expect(
      parseUsageFrame({
        model: 'gpt-5',
        last: { input: 100, cache_read: 900, cache_write: 5, output: 40 },
        total: { input: 1000, cache_read: 9000, cache_write: 0, output: 400 },
        window: 272000,
      }),
    ).toEqual({
      model: 'gpt-5',
      contextTokens: 1005,
      window: 272000,
      totalInputTokens: 10000,
      totalOutputTokens: 400,
    });
  });

  it('reports only what the runtime sent', () => {
    expect(parseUsageFrame({ last: null, total: null, window: 1000 })).toEqual({ window: 1000 });
    expect(parseUsageFrame({ last: null, total: null, window: null })).toBeNull();
  });
});

describe('delivery', () => {
  it('maps the broker steering state of a turn', () => {
    expect(deliveryFromSteeringState('pending')).toEqual({ state: 'queued' });
    expect(deliveryFromSteeringState('active')).toEqual({ state: 'active' });
    expect(deliveryFromSteeringState('failed')).toEqual({ state: 'failed' });
    expect(deliveryFromSteeringState(undefined)).toBeUndefined();
    expect(deliveryFromSteeringState('something-new')).toBeUndefined();
  });

  it('reads each delivery frame, naming the message by id, msg_id or request id', () => {
    expect(parseDeliveryFrame({ type: 'user_active', id: 'm1', request_id: 'r1' })).toEqual({
      ids: ['m1', 'r1'],
      delivery: { state: 'active' },
    });
    expect(parseDeliveryFrame({ type: 'user_consumed', msg_id: 'm2' })).toEqual({
      ids: ['m2'],
      delivery: { state: 'active' },
    });
    expect(parseDeliveryFrame({ type: 'user_delivered', id: 'm3' })?.delivery.state).toBe(
      'delivered',
    );
    expect(
      parseDeliveryFrame({ type: 'user_delivery_pending', id: 'm4', error: 'not persisted' }),
    ).toEqual({ ids: ['m4'], delivery: { state: 'unconfirmed', error: 'not persisted' } });
    expect(
      parseDeliveryFrame({ type: 'user_delivery_failed', request_id: 'r5', error: 'pane gone' }),
    ).toEqual({ ids: ['r5'], delivery: { state: 'failed', error: 'pane gone' } });
  });

  it('ignores other frames and frames that name no message', () => {
    expect(parseDeliveryFrame({ type: 'assistant', id: 'x' })).toBeNull();
    expect(parseDeliveryFrame({ type: 'user_active' })).toBeNull();
  });

  it('only moves delivery forward', () => {
    expect(advanceDelivery(undefined, { state: 'queued' })).toEqual({ state: 'queued' });
    expect(advanceDelivery({ state: 'queued' }, { state: 'delivered' })).toEqual({
      state: 'delivered',
    });
    expect(advanceDelivery({ state: 'active' }, { state: 'failed', error: 'late' })).toEqual({
      state: 'active',
    });
    expect(advanceDelivery({ state: 'delivered' }, { state: 'queued' })).toEqual({
      state: 'delivered',
    });
    expect(advanceDelivery({ state: 'failed' }, { state: 'unconfirmed' })).toEqual({
      state: 'failed',
    });
    expect(advanceDelivery({ state: 'unconfirmed' }, { state: 'delivered' })).toEqual({
      state: 'delivered',
    });
  });
});

describe('parseMessageAuthor', () => {
  it('reads the broker author shape', () => {
    expect(parseMessageAuthor({ user_id: ' u-1 ', display_name: ' Ada ' })).toEqual({
      userId: 'u-1',
      displayName: 'Ada',
    });
  });

  it('keeps an author whose name the proxy did not know, with an empty name', () => {
    expect(parseMessageAuthor({ user_id: 'u-1' })).toEqual({ userId: 'u-1', displayName: '' });
    expect(parseMessageAuthor({ user_id: 'u-1', display_name: 7 })).toEqual({
      userId: 'u-1',
      displayName: '',
    });
  });

  it.each([undefined, null, 'u-1', 3, [], {}, { user_id: '' }, { user_id: '  ' }, { user_id: 4 }])(
    'treats %j as no author',
    (raw) => {
      expect(parseMessageAuthor(raw)).toBeUndefined();
    },
  );
});
