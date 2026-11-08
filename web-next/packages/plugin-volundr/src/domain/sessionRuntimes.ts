/**
 * "Runs as" — what a host runs every session as.
 *
 * A Forge host runs exactly one runtime, set on the server, and never mixes runtimes in
 * a pool, so a launch never chooses one. `GET /api/v1/forge/session-runtimes` names it
 * (an empty list when the host declares none, which launches as it always has). The
 * launch only asks for extra access the runtime offers, as `workload_config.runtime_grants`.
 */

export type SessionRuntimeKind = 'container' | 'openshell' | 'host';
export type SessionGrant = 'sudo' | 'docker';

export interface RuntimeReadiness {
  /** `warm`: a prepared machine is free. `boot`: one boots first. `full`: no capacity. */
  state: 'warm' | 'boot' | 'full';
  /** Free slots on prepared machines that can take a session now. */
  warm: number;
  /** Where capacity is raised when the pool is full. */
  remedy: string | null;
}

export interface SessionRuntimeOffer {
  /** What every session on this host runs as. */
  runtime: SessionRuntimeKind;
  /** Extra access this runtime can give a session. */
  grants: SessionGrant[];
  readiness: RuntimeReadiness;
}

export const RUNTIME_LABELS: Record<SessionRuntimeKind, { name: string; summary: string }> = {
  container: { name: 'Container', summary: 'Own image, own root' },
  openshell: { name: 'OpenShell', summary: 'Policy sandbox' },
  host: { name: 'On the VM', summary: "VM's own tools" },
};

export const GRANT_LABELS: Record<SessionGrant, string> = {
  sudo: 'sudo',
  docker: 'Docker',
};

/** Grants the person picked that the host's runtime still offers. */
export function offeredGrants(
  offer: SessionRuntimeOffer | undefined,
  picked: readonly SessionGrant[],
): SessionGrant[] {
  if (!offer) return [];
  return offer.grants.filter((grant) => picked.includes(grant));
}

/** The workload_config entries a launch adds: only the grants asked for. */
export function runsAsWorkloadConfig(grants: readonly SessionGrant[]): Record<string, unknown> {
  return grants.length ? { runtime_grants: [...grants] } : {};
}

/** One line on how soon a session starts. */
export function readinessLine(readiness: RuntimeReadiness): string {
  switch (readiness.state) {
    case 'warm':
      return readiness.warm === 1
        ? 'Free slot ready, starts in seconds'
        : `${readiness.warm} free slots ready, starts in seconds`;
    case 'boot':
      return 'No free slot: a VM boots first, which takes a few minutes';
    case 'full':
      return `No capacity for a new session${readiness.remedy ? `: ${readiness.remedy}` : ''}`;
  }
}
