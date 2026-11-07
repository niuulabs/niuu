/**
 * "Runs as" — the runtimes a launch on a host may choose between.
 *
 * A VM Forge lists them (`GET /api/v1/forge/session-runtimes`); a host that lists none
 * offers no choice and runs its sessions the way that Forge always runs them. The
 * choice goes to the session as `workload_config.execution_profile`, and grants, only
 * when the chosen runtime offers them, as `workload_config.runtime_grants`.
 */

export type SessionRuntimeKind = 'container' | 'openshell' | 'host';
export type SessionGrant = 'sudo' | 'docker';

export interface RuntimeReadiness {
  /** `warm`: a prepared machine is free. `boot`: one boots first. `full`: no capacity. */
  state: 'warm' | 'boot' | 'full';
  /** Free prepared machines that can take this runtime now. */
  warm: number;
  /** Where capacity is raised when the pool is full. */
  remedy: string | null;
}

export interface SessionRuntimeOffer {
  runtime: SessionRuntimeKind;
  /** The exact execution (`id@revision`) this choice selects. */
  executionProfile: string;
  isDefault: boolean;
  grants: SessionGrant[];
  readiness: RuntimeReadiness;
}

export interface RunsAsSelection {
  executionProfile: string;
  grants: SessionGrant[];
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

/** The offer a selection names, else the host's default, else its first. */
export function selectedOffer(
  offers: readonly SessionRuntimeOffer[],
  selection: RunsAsSelection | null,
): SessionRuntimeOffer | undefined {
  return (
    offers.find((offer) => offer.executionProfile === selection?.executionProfile) ??
    offers.find((offer) => offer.isDefault) ??
    offers[0]
  );
}

/** Grants the selection keeps: only those its runtime offers. */
export function selectedGrants(
  offer: SessionRuntimeOffer | undefined,
  selection: RunsAsSelection | null,
): SessionGrant[] {
  if (!offer || selection?.executionProfile !== offer.executionProfile) return [];
  return selection.grants.filter((grant) => offer.grants.includes(grant));
}

/** The workload_config entries the choice adds; none when the host offers no choice. */
export function runsAsWorkloadConfig(
  offer: SessionRuntimeOffer | undefined,
  grants: readonly SessionGrant[],
): Record<string, unknown> {
  if (!offer) return {};
  return {
    execution_profile: offer.executionProfile,
    ...(grants.length ? { runtime_grants: [...grants] } : {}),
  };
}

/** One line on how soon a session with this choice starts. */
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
