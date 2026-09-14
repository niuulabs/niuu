import { describe, expect, it } from 'vitest';
import type { Ravn } from './ravn';
import {
  canCreateResidentSession,
  canDeleteResidentSession,
  canListResidentSessions,
  canRestartResident,
  canResumeResident,
  canSuspendResident,
  isResidentRavn,
  isResidentSuspended,
  nameForRavn,
  ravnKey,
  realmSlugForRavn,
} from './residentActions';

function makeRavn(overrides: Partial<Ravn> = {}): Ravn {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    personaName: 'realm-lexi-api',
    status: 'active',
    model: 'gpt-5.6',
    createdAt: '2026-07-13T10:00:00Z',
    managed: true,
    kind: 'resident',
    desiredState: 'running',
    observedState: 'active',
    capabilities: ['runtime.restart', 'runtime.suspend', 'session.list', 'session.create'],
    ...overrides,
  } as Ravn;
}

describe('nameForRavn', () => {
  it('prefers the resident name, then the persona, then a short id', () => {
    expect(nameForRavn(makeRavn({ residentName: 'Muninn' }))).toBe('Muninn');
    expect(nameForRavn(makeRavn())).toBe('realm-lexi-api');
    expect(nameForRavn(makeRavn({ personaName: '' }))).toBe('11111111');
  });
});

describe('ravnKey', () => {
  it('scopes the id by instance, because ids repeat across instances', () => {
    expect(ravnKey({ id: 'a', instanceId: 'target a' })).toBe('target%20a:a');
    expect(ravnKey({ id: 'a' })).toBe('a');
  });
});

describe('isResidentRavn', () => {
  it('trusts kind when present and falls back to the managed flag', () => {
    expect(isResidentRavn({ kind: 'resident' })).toBe(true);
    expect(isResidentRavn({ kind: 'persona', managed: true })).toBe(false);
    expect(isResidentRavn({ managed: true })).toBe(true);
    expect(isResidentRavn({})).toBe(false);
  });
});

describe('realmSlugForRavn', () => {
  it('uses the resident name, else the persona without its realm prefix', () => {
    expect(realmSlugForRavn({ residentName: 'lexi-api', personaName: 'realm-other' })).toBe(
      'lexi-api',
    );
    expect(realmSlugForRavn({ personaName: 'realm-lexi-api' })).toBe('lexi-api');
    expect(realmSlugForRavn({ personaName: 'builder' })).toBe('builder');
    expect(realmSlugForRavn({ personaName: '' })).toBe('');
  });
});

describe('lifecycle gating', () => {
  it('allows a restart only for a managed, running resident that is up or failed', () => {
    expect(canRestartResident(makeRavn())).toBe(true);
    expect(canRestartResident(makeRavn({ observedState: 'failed' }))).toBe(true);
    expect(canRestartResident(makeRavn({ observedState: 'deploying' }))).toBe(false);
    expect(canRestartResident(makeRavn({ desiredState: 'suspended' }))).toBe(false);
    expect(canRestartResident(makeRavn({ managed: false }))).toBe(false);
    expect(canRestartResident(makeRavn({ capabilities: ['chat'] }))).toBe(false);
  });

  it('offers suspend while active and resume once suspended, never both', () => {
    const active = makeRavn();
    expect(canSuspendResident(active)).toBe(true);
    expect(canResumeResident(active)).toBe(false);

    const suspended = makeRavn({ observedState: 'suspended', desiredState: 'suspended' });
    expect(isResidentSuspended(suspended)).toBe(true);
    expect(canSuspendResident(suspended)).toBe(false);
    expect(canResumeResident(suspended)).toBe(true);
  });

  it('needs the suspend capability for either direction', () => {
    const noCapability = makeRavn({ capabilities: ['runtime.restart'] });
    expect(canSuspendResident(noCapability)).toBe(false);
    expect(canResumeResident(makeRavn({ capabilities: [], observedState: 'suspended' }))).toBe(
      false,
    );
  });

  it('gates session commands on the capability and an active backend', () => {
    expect(canListResidentSessions(makeRavn())).toBe(true);
    expect(canCreateResidentSession(makeRavn())).toBe(true);
    expect(canDeleteResidentSession(makeRavn())).toBe(false);
    expect(canCreateResidentSession(makeRavn({ observedState: 'suspended' }))).toBe(false);
  });
});
