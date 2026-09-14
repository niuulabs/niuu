/**
 * Resident identity and lifecycle gating.
 *
 * These rules decide which controls a managed resident may show. They live here
 * — not in a component — so the fleet detail panel and the Residents board agree
 * on what a resident can do, and so the rules can be tested on their own.
 */

import type { Ravn } from './ravn';

/** Persona names for realm residents are namespaced with this prefix. */
const REALM_PERSONA_PREFIX = 'realm-';

/** A restart only makes sense while the backend has something to restart. */
const RESTARTABLE_OBSERVED_STATES = ['active', 'failed'];

/** Display name: the resident's own name, else its persona, else a short id. */
export function nameForRavn(ravn: Pick<Ravn, 'id' | 'personaName' | 'residentName'>): string {
  return ravn.residentName || ravn.personaName || ravn.id.slice(0, 8);
}

/** Stable list key — ids are only unique within an instance. */
export function ravnKey(ravn: Pick<Ravn, 'id' | 'instanceId'>): string {
  return ravn.instanceId ? `${encodeURIComponent(ravn.instanceId)}:${ravn.id}` : ravn.id;
}

/**
 * A resident is a long-lived ravn that keeps something. Records written before
 * `kind` existed only carry `managed`.
 */
export function isResidentRavn(ravn: Pick<Ravn, 'kind' | 'managed'>): boolean {
  if (ravn.kind) return ravn.kind === 'resident';
  return ravn.managed === true;
}

/** The realm slug this resident keeps, as far as its own naming says. */
export function realmSlugForRavn(ravn: Pick<Ravn, 'residentName' | 'personaName'>): string {
  if (ravn.residentName) return ravn.residentName;
  if (!ravn.personaName) return '';
  if (!ravn.personaName.startsWith(REALM_PERSONA_PREFIX)) return ravn.personaName;
  return ravn.personaName.slice(REALM_PERSONA_PREFIX.length);
}

function residentIsActive(ravn: Pick<Ravn, 'observedState'>): boolean {
  return ravn.observedState === 'active';
}

function hasCapability(
  ravn: Pick<Ravn, 'managed' | 'capabilities'>,
  capability: NonNullable<Ravn['capabilities']>[number],
): boolean {
  return Boolean(ravn.managed && ravn.capabilities?.includes(capability));
}

export function canRestartResident(
  ravn: Pick<Ravn, 'managed' | 'capabilities' | 'desiredState' | 'observedState'>,
): boolean {
  return (
    hasCapability(ravn, 'runtime.restart') &&
    ravn.desiredState === 'running' &&
    RESTARTABLE_OBSERVED_STATES.includes(ravn.observedState ?? '')
  );
}

/** Suspended either because the operator asked for it or because it already is. */
export function isResidentSuspended(ravn: Pick<Ravn, 'desiredState' | 'observedState'>): boolean {
  return ravn.observedState === 'suspended' || ravn.desiredState === 'suspended';
}

export function canSuspendResident(
  ravn: Pick<Ravn, 'managed' | 'capabilities' | 'desiredState' | 'observedState'>,
): boolean {
  return (
    hasCapability(ravn, 'runtime.suspend') && residentIsActive(ravn) && !isResidentSuspended(ravn)
  );
}

export function canResumeResident(
  ravn: Pick<Ravn, 'managed' | 'capabilities' | 'desiredState' | 'observedState'>,
): boolean {
  return hasCapability(ravn, 'runtime.suspend') && isResidentSuspended(ravn);
}

export function canListResidentSessions(
  ravn: Pick<Ravn, 'managed' | 'capabilities' | 'observedState'>,
): boolean {
  return hasCapability(ravn, 'session.list') && residentIsActive(ravn);
}

export function canCreateResidentSession(
  ravn: Pick<Ravn, 'managed' | 'capabilities' | 'observedState'>,
): boolean {
  return hasCapability(ravn, 'session.create') && residentIsActive(ravn);
}

export function canDeleteResidentSession(
  ravn: Pick<Ravn, 'managed' | 'capabilities' | 'observedState'>,
): boolean {
  return hasCapability(ravn, 'session.delete') && residentIsActive(ravn);
}
