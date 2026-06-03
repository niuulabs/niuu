import { describe, expect, it } from 'vitest';
import { createSeedValkyrieDashboard } from '../adapters/mock';
import { selectEnvironmentSlice, selectFlockLearnings } from './selectors';

describe('valkyrie selectors', () => {
  it('selects environment-owned state without crossing environments', () => {
    const dashboard = createSeedValkyrieDashboard();
    const slice = selectEnvironmentSlice(dashboard, 'env-k8s-valhalla');

    expect(slice.valkyries.map((entry) => entry.id)).toContain('valkyrie-valhalla-sigrun');
    expect(slice.signals.every((entry) => entry.environmentId === 'env-k8s-valhalla')).toBe(true);
    expect(slice.flock?.id).toBe('flock-k8s');
  });

  it('selects peer learnings through existing flock membership', () => {
    const dashboard = createSeedValkyrieDashboard();
    const learnings = selectFlockLearnings(dashboard, 'flock-k8s');

    expect(learnings.map((entry) => entry.id)).toContain('learn-k8s-oom-canary');
    expect(
      learnings.every((entry) => entry.scope === 'flock' || entry.targetFlockId === 'flock-k8s'),
    ).toBe(true);
  });
});
