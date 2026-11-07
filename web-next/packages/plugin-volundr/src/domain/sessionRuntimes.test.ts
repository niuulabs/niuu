import { describe, expect, it } from 'vitest';
import {
  readinessLine,
  runsAsWorkloadConfig,
  selectedGrants,
  selectedOffer,
  type SessionRuntimeOffer,
} from './sessionRuntimes';

const container: SessionRuntimeOffer = {
  runtime: 'container',
  executionProfile: 'vm-container@1',
  isDefault: false,
  grants: [],
  readiness: { state: 'warm', warm: 1, remedy: null },
};
const host: SessionRuntimeOffer = {
  runtime: 'host',
  executionProfile: 'vm-host@1',
  isDefault: true,
  grants: ['sudo', 'docker'],
  readiness: { state: 'boot', warm: 0, remedy: null },
};

describe('selectedOffer', () => {
  it('takes the selection, else the default, else the first offer', () => {
    expect(
      selectedOffer([container, host], { executionProfile: 'vm-container@1', grants: [] }),
    ).toBe(container);
    expect(selectedOffer([container, host], null)).toBe(host);
    expect(selectedOffer([container], { executionProfile: 'gone@1', grants: [] })).toBe(container);
    expect(selectedOffer([], null)).toBeUndefined();
  });
});

describe('selectedGrants', () => {
  it('keeps only grants the selected runtime offers', () => {
    expect(
      selectedGrants(host, { executionProfile: 'vm-host@1', grants: ['sudo', 'docker'] }),
    ).toEqual(['sudo', 'docker']);
    expect(
      selectedGrants(container, { executionProfile: 'vm-container@1', grants: ['sudo'] }),
    ).toEqual([]);
    expect(selectedGrants(host, { executionProfile: 'vm-container@1', grants: ['sudo'] })).toEqual(
      [],
    );
    expect(selectedGrants(undefined, null)).toEqual([]);
  });
});

describe('runsAsWorkloadConfig', () => {
  it('names the execution and only the grants asked for', () => {
    expect(runsAsWorkloadConfig(undefined, [])).toEqual({});
    expect(runsAsWorkloadConfig(container, [])).toEqual({ execution_profile: 'vm-container@1' });
    expect(runsAsWorkloadConfig(host, ['docker'])).toEqual({
      execution_profile: 'vm-host@1',
      runtime_grants: ['docker'],
    });
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
