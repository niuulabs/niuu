import { describe, expect, it } from 'vitest';
import {
  offeredGrants,
  readinessLine,
  runsAsWorkloadConfig,
  type SessionRuntimeOffer,
} from './sessionRuntimes';

const container: SessionRuntimeOffer = {
  runtime: 'container',
  grants: [],
  readiness: { state: 'warm', warm: 1, remedy: null },
};
const host: SessionRuntimeOffer = {
  runtime: 'host',
  grants: ['sudo', 'docker'],
  readiness: { state: 'boot', warm: 0, remedy: null },
};

describe('offeredGrants', () => {
  it('keeps only grants the host runtime offers', () => {
    expect(offeredGrants(host, ['sudo', 'docker'])).toEqual(['sudo', 'docker']);
    expect(offeredGrants(host, ['docker'])).toEqual(['docker']);
    expect(offeredGrants(container, ['sudo'])).toEqual([]);
    expect(offeredGrants(undefined, ['sudo'])).toEqual([]);
  });
});

describe('runsAsWorkloadConfig', () => {
  it('sends only the grants asked for, never an execution', () => {
    expect(runsAsWorkloadConfig([])).toEqual({});
    expect(runsAsWorkloadConfig(['docker'])).toEqual({ runtime_grants: ['docker'] });
  });
});

describe('readinessLine', () => {
  it('says how soon the session starts', () => {
    expect(readinessLine({ state: 'warm', warm: 1, remedy: null })).toBe(
      'Free slot ready, starts in seconds',
    );
    expect(readinessLine({ state: 'warm', warm: 3, remedy: null })).toBe(
      '3 free slots ready, starts in seconds',
    );
    expect(readinessLine({ state: 'boot', warm: 0, remedy: null })).toMatch(/a VM boots first/);
    expect(readinessLine({ state: 'full', warm: 0, remedy: 'raise max machines' })).toBe(
      'No capacity for a new session: raise max machines',
    );
    expect(readinessLine({ state: 'full', warm: 0, remedy: null })).toBe(
      'No capacity for a new session',
    );
  });
});
