import { describe, it, expect } from 'vitest';
import { resolveRoute } from './routing';
import type { WriteRoutingRule } from './routing';

function makeRule(
  overrides: Partial<WriteRoutingRule> & { prefix: string; mountName: string },
): WriteRoutingRule {
  return {
    id: `rule-${overrides.prefix.replace(/\//g, '-')}`,
    priority: 10,
    active: true,
    ...overrides,
  };
}

describe('resolveRoute', () => {
  it('returns null mountName when no rules exist', () => {
    const result = resolveRoute([], '/arch/overview');
    expect(result.mountName).toBeNull();
    expect(result.matchedRule).toBeNull();
  });

  it('includes a no-match reason when rules are empty', () => {
    const result = resolveRoute([], '/arch/overview');
    expect(result.reason).toMatch(/no active rule/);
  });

  it('matches a rule whose prefix is a prefix of the path', () => {
    const rules = [makeRule({ prefix: '/infra', mountName: 'platform', priority: 5 })];
    const result = resolveRoute(rules, '/infra/k8s');
    expect(result.mountName).toBe('platform');
    expect(result.matchedRule?.prefix).toBe('/infra');
  });

  it('picks the rule with the lowest priority when multiple match', () => {
    const rules = [
      makeRule({ prefix: '/infra', mountName: 'platform', priority: 10 }),
      makeRule({ id: 'catch-all', prefix: '/', mountName: 'shared', priority: 99 }),
    ];
    const result = resolveRoute(rules, '/infra/k8s');
    expect(result.mountName).toBe('platform');
  });

  it('falls through to a lower-priority catch-all rule', () => {
    const rules = [
      makeRule({ prefix: '/infra', mountName: 'platform', priority: 5 }),
      makeRule({ id: 'catch-all', prefix: '/', mountName: 'shared', priority: 99 }),
    ];
    const result = resolveRoute(rules, '/api/overview');
    expect(result.mountName).toBe('shared');
  });

  it('ignores inactive rules', () => {
    const rules = [
      makeRule({ prefix: '/infra', mountName: 'platform', active: false }),
      makeRule({ id: 'catch-all', prefix: '/', mountName: 'shared', priority: 99 }),
    ];
    const result = resolveRoute(rules, '/infra/k8s');
    expect(result.mountName).toBe('shared');
  });

  it('returns no match when only inactive rules exist', () => {
    const rules = [makeRule({ prefix: '/infra', mountName: 'platform', active: false })];
    const result = resolveRoute(rules, '/infra/k8s');
    expect(result.mountName).toBeNull();
  });

  it('includes the matched rule in the result', () => {
    const rules = [makeRule({ prefix: '/api', mountName: 'shared' })];
    const result = resolveRoute(rules, '/api/overview');
    expect(result.matchedRule).not.toBeNull();
    expect(result.matchedRule?.mountName).toBe('shared');
  });

  it('includes a human-readable reason explaining the match', () => {
    const rules = [makeRule({ prefix: '/api', mountName: 'shared', priority: 3 })];
    const result = resolveRoute(rules, '/api/overview');
    expect(result.reason).toContain('/api');
    expect(result.reason).toContain('shared');
    expect(result.reason).toContain('3');
  });

  it('returns the path unchanged in the result', () => {
    const result = resolveRoute([], '/some/path');
    expect(result.path).toBe('/some/path');
  });

  it('matches the root catch-all prefix "/"', () => {
    const rules = [makeRule({ prefix: '/', mountName: 'shared', priority: 99 })];
    const result = resolveRoute(rules, '/entities/tyr');
    expect(result.mountName).toBe('shared');
  });

  it('exact prefix match on the path itself', () => {
    const rules = [makeRule({ prefix: '/arch/overview', mountName: 'local', priority: 1 })];
    const result = resolveRoute(rules, '/arch/overview');
    expect(result.mountName).toBe('local');
  });

  it('does not match a rule whose prefix extends beyond the path', () => {
    const rules = [makeRule({ prefix: '/arch/overview/deep', mountName: 'local', priority: 1 })];
    const result = resolveRoute(rules, '/arch/overview');
    expect(result.mountName).toBeNull();
  });

  it('evaluates priority correctly with three rules', () => {
    const rules = [
      makeRule({ id: 'r1', prefix: '/', mountName: 'shared', priority: 100 }),
      makeRule({ id: 'r2', prefix: '/api', mountName: 'api-mount', priority: 20 }),
      makeRule({ id: 'r3', prefix: '/api/v2', mountName: 'api-v2', priority: 5 }),
    ];
    expect(resolveRoute(rules, '/api/v2/endpoints').mountName).toBe('api-v2');
    expect(resolveRoute(rules, '/api/v1/endpoints').mountName).toBe('api-mount');
    expect(resolveRoute(rules, '/entities/tyr').mountName).toBe('shared');
  });
});
