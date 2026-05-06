import { describe, it, expect } from 'vitest';
import {
  personaRoleSchema,
  llmConfigSchema,
  consumedEventSchema,
  producedEventSchema,
  fanInStrategySchema,
  personaSchema,
  quorumParamsSchema,
  weightedScoreParamsSchema,
} from './persona';

// ---------------------------------------------------------------------------
// personaRoleSchema
// ---------------------------------------------------------------------------

describe('personaRoleSchema', () => {
  it.each([
    'arbiter',
    'audit',
    'autonomy',
    'build',
    'coord',
    'gate',
    'index',
    'investigate',
    'knowledge',
    'observe',
    'plan',
    'qa',
    'report',
    'review',
    'ship',
    'verify',
    'write',
  ])('accepts role "%s"', (role) => {
    expect(personaRoleSchema.parse(role)).toBe(role);
  });

  it('rejects an unknown role', () => {
    expect(() => personaRoleSchema.parse('destroy')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// llmConfigSchema
// ---------------------------------------------------------------------------

describe('llmConfigSchema', () => {
  it('round-trips a full config', () => {
    const input = { alias: 'claude-sonnet-4-6', thinking: true, maxTokens: 4096, temperature: 0.7 };
    expect(llmConfigSchema.parse(input)).toEqual(input);
  });

  it('round-trips without optional temperature', () => {
    const input = { alias: 'claude-haiku-4-5', thinking: false, maxTokens: 2048 };
    const result = llmConfigSchema.parse(input);
    expect(result.temperature).toBeUndefined();
  });

  it('rejects zero maxTokens', () => {
    expect(() => llmConfigSchema.parse({ alias: 'x', thinking: false, maxTokens: 0 })).toThrow();
  });

  it('rejects temperature > 2', () => {
    expect(() =>
      llmConfigSchema.parse({ alias: 'x', thinking: false, maxTokens: 1, temperature: 3 }),
    ).toThrow();
  });

  it('rejects temperature < 0', () => {
    expect(() =>
      llmConfigSchema.parse({ alias: 'x', thinking: false, maxTokens: 1, temperature: -0.1 }),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// consumedEventSchema
// ---------------------------------------------------------------------------

describe('consumedEventSchema', () => {
  it('round-trips minimal', () => {
    expect(consumedEventSchema.parse({ name: 'code.changed' })).toEqual({ name: 'code.changed' });
  });

  it('round-trips full', () => {
    const input = { name: 'review.done', injects: ['summary'], trust: 0.8 };
    expect(consumedEventSchema.parse(input)).toEqual(input);
  });

  it('rejects trust > 1', () => {
    expect(() => consumedEventSchema.parse({ name: 'x', trust: 1.5 })).toThrow();
  });

  it('rejects trust < 0', () => {
    expect(() => consumedEventSchema.parse({ name: 'x', trust: -0.1 })).toThrow();
  });

  it('rejects empty name', () => {
    expect(() => consumedEventSchema.parse({ name: '' })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// producedEventSchema
// ---------------------------------------------------------------------------

describe('producedEventSchema', () => {
  it('round-trips', () => {
    const input = { event: 'build.artifact', schema: { url: 'string', size: 'number' } };
    expect(producedEventSchema.parse(input)).toEqual(input);
  });

  it('accepts empty schema', () => {
    expect(producedEventSchema.parse({ event: 'ping', schema: {} })).toEqual({
      event: 'ping',
      schema: {},
    });
  });

  it('rejects empty event name', () => {
    expect(() => producedEventSchema.parse({ event: '', schema: {} })).toThrow();
  });

  it('rejects an invalid field type in schema', () => {
    expect(() =>
      producedEventSchema.parse({ event: 'build.artifact', schema: { url: 'URL' } }),
    ).toThrow();
  });
});

// ---------------------------------------------------------------------------
// quorumParamsSchema
// ---------------------------------------------------------------------------

describe('quorumParamsSchema', () => {
  it('round-trips minimal', () => {
    expect(quorumParamsSchema.parse({ n: 2, of: 3 })).toEqual({ n: 2, of: 3 });
  });

  it('round-trips with windowMs', () => {
    const input = { n: 2, of: 4, windowMs: 5000 };
    expect(quorumParamsSchema.parse(input)).toEqual(input);
  });

  it('rejects n < 1', () => {
    expect(() => quorumParamsSchema.parse({ n: 0, of: 1 })).toThrow();
  });

  it('rejects of < 1', () => {
    expect(() => quorumParamsSchema.parse({ n: 1, of: 0 })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// weightedScoreParamsSchema
// ---------------------------------------------------------------------------

describe('weightedScoreParamsSchema', () => {
  it('round-trips with weights', () => {
    const input = { weights: { planner: 0.6, builder: 0.4 } };
    expect(weightedScoreParamsSchema.parse(input)).toEqual(input);
  });

  it('round-trips without weights', () => {
    expect(weightedScoreParamsSchema.parse({})).toEqual({});
  });

  it('rejects a weight > 1', () => {
    expect(() => weightedScoreParamsSchema.parse({ weights: { p: 1.5 } })).toThrow();
  });

  it('rejects a weight < 0', () => {
    expect(() => weightedScoreParamsSchema.parse({ weights: { p: -0.1 } })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// fanInStrategySchema
// ---------------------------------------------------------------------------

describe('fanInStrategySchema', () => {
  it('accepts all_must_pass', () => {
    const r = fanInStrategySchema.parse({ strategy: 'all_must_pass', params: {} });
    expect(r.strategy).toBe('all_must_pass');
  });

  it('accepts any_passes', () => {
    expect(fanInStrategySchema.parse({ strategy: 'any_passes', params: {} }).strategy).toBe(
      'any_passes',
    );
  });

  it('accepts quorum with required params', () => {
    const r = fanInStrategySchema.parse({ strategy: 'quorum', params: { n: 2, of: 3 } });
    expect(r.strategy).toBe('quorum');
  });

  it('accepts merge', () => {
    expect(fanInStrategySchema.parse({ strategy: 'merge', params: {} }).strategy).toBe('merge');
  });

  it('accepts first_wins', () => {
    expect(fanInStrategySchema.parse({ strategy: 'first_wins', params: {} }).strategy).toBe(
      'first_wins',
    );
  });

  it('accepts weighted_score with optional weights', () => {
    const r = fanInStrategySchema.parse({
      strategy: 'weighted_score',
      params: { weights: { planner: 0.7 } },
    });
    expect(r.strategy).toBe('weighted_score');
  });

  it('accepts weighted_score without weights', () => {
    const r = fanInStrategySchema.parse({ strategy: 'weighted_score', params: {} });
    expect(r.strategy).toBe('weighted_score');
  });

  it('rejects quorum missing required params', () => {
    expect(() => fanInStrategySchema.parse({ strategy: 'quorum', params: {} })).toThrow();
  });

  it('rejects an unknown strategy', () => {
    expect(() => fanInStrategySchema.parse({ strategy: 'unknown_strategy', params: {} })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// personaSchema
// ---------------------------------------------------------------------------

const minimalPersona = {
  name: 'Skald',
  role: 'build',
  color: '#6366f1',
  letter: 'S',
  summary: 'Writes and compiles code',
  description: 'A detailed builder persona.',
  llm: { alias: 'claude-sonnet-4-6', thinking: false, maxTokens: 4096 },
  permissionMode: 'default',
  allowed: ['read', 'write'],
  forbidden: ['bash'],
  produces: { event: 'build.artifact', schema: { url: 'string' } },
  consumes: { events: [{ name: 'plan.done', trust: 0.9 }] },
} as const;

describe('personaSchema', () => {
  it('round-trips a minimal persona', () => {
    const result = personaSchema.parse(minimalPersona);
    expect(result.name).toBe('Skald');
    expect(result.role).toBe('build');
    expect(result.fanIn).toBeUndefined();
    expect(result.mimirWriteRouting).toBeUndefined();
  });

  it('round-trips with fanIn and mimirWriteRouting', () => {
    const input = {
      ...minimalPersona,
      fanIn: { strategy: 'all_must_pass', params: {} },
      mimirWriteRouting: 'shared',
    };
    const result = personaSchema.parse(input);
    expect(result.fanIn?.strategy).toBe('all_must_pass');
    expect(result.mimirWriteRouting).toBe('shared');
  });

  it('round-trips with quorum fanIn', () => {
    const input = {
      ...minimalPersona,
      fanIn: { strategy: 'quorum', params: { n: 2, of: 3, windowMs: 10000 } },
    };
    const result = personaSchema.parse(input);
    expect(result.fanIn?.strategy).toBe('quorum');
  });

  it('round-trips with weighted_score fanIn', () => {
    const input = {
      ...minimalPersona,
      fanIn: { strategy: 'weighted_score', params: { weights: { skald: 0.8 } } },
    };
    const result = personaSchema.parse(input);
    expect(result.fanIn?.strategy).toBe('weighted_score');
  });

  it('rejects letter longer than 1 char', () => {
    expect(() => personaSchema.parse({ ...minimalPersona, letter: 'AB' })).toThrow();
  });

  it('rejects an invalid permissionMode', () => {
    expect(() => personaSchema.parse({ ...minimalPersona, permissionMode: 'nuclear' })).toThrow();
  });

  it('rejects an invalid role', () => {
    expect(() => personaSchema.parse({ ...minimalPersona, role: 'destroyer' })).toThrow();
  });

  it('rejects invalid mimirWriteRouting', () => {
    expect(() => personaSchema.parse({ ...minimalPersona, mimirWriteRouting: 'remote' })).toThrow();
  });

  it('accepts all valid mimir write routing values', () => {
    for (const r of ['local', 'shared', 'domain'] as const) {
      expect(
        personaSchema.parse({ ...minimalPersona, mimirWriteRouting: r }).mimirWriteRouting,
      ).toBe(r);
    }
  });
});
