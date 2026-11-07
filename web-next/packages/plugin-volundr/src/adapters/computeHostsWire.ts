/**
 * Wire shapes of the Forge compute-hosts routes (`/api/v1/forge/compute/hosts…`) and
 * their conversion to the domain. A body that is not the documented shape throws: a
 * half-read host list would show healthy hosts that are not.
 */
import type {
  ComputeConnector,
  ComputeHost,
  ComputeHostsOverview,
  ComputeRuntime,
  ComputeSummary,
  HostFacts,
  NewPairingCode,
  PairingCode,
  PairingCodeRequest,
  RemoveHostResult,
  SuggestedSlots,
  WarmTarget,
} from '../domain/computeHosts';

interface HostFactsPayload {
  cpus?: number | null;
  memory_mib?: number | null;
  disk_free_mib?: number | null;
  kernel?: string | null;
  arch?: string | null;
  cgroup_v2?: boolean | null;
  gpus?: number | null;
  os?: string | null;
}

export interface ComputeHostPayload {
  id: string;
  name: string;
  tags: string[];
  state: ComputeHost['state'];
  health: ComputeHost['health'];
  service: ComputeHost['service'];
  remove_when_empty: boolean;
  execution: string;
  runtime: string;
  connector: string;
  connection: {
    kind: string;
    target: string;
    connected: boolean;
    since: string | null;
    last_error: string | null;
  };
  slots: number | null;
  sessions: Array<{
    slot: number;
    session_id: string;
    state: ComputeHost['sessions'][number]['state'];
  }>;
  slots_free: number;
  facts: HostFactsPayload | null;
  suggested_slots: SuggestedSlots | null;
  findings: string[];
  host_key_fingerprint: string;
  registered_at: string;
  registered_by: string;
  last_checked_at: string | null;
}

interface WarmTargetPayload {
  tags: string[];
  match: WarmTarget['match'];
  min: number;
  free: number;
  met: boolean;
}

export interface PairingCodePayload {
  id: string;
  execution: string;
  connector: string;
  tags: string[];
  slots: number | null;
  expires_at: string;
  created_by: string;
  consumed_by_host_id: string | null;
}

export interface NewPairingCodePayload extends PairingCodePayload {
  code: string;
  command: string;
  /** Null when the join is pinned: wget cannot check a pin. */
  command_wget: string | null;
  join_url: string;
  /** The join listener's `curl --pinnedpubkey` pin; null when Forge's API serves the join. */
  pin: string | null;
}

export interface ComputeHostsPayload {
  pool_id: string;
  hosts: ComputeHostPayload[];
  summary: {
    hosts: number;
    by_state: ComputeSummary['byState'];
    slots_total: number;
    slots_free: number;
    free_by_tag_set: ComputeSummary['freeByTagSet'];
    warm_targets: WarmTargetPayload[];
  };
  connectors: Array<{ binding_id: string; kind: string; label: string }>;
  runtimes: Array<{
    execution: string;
    runtime: string;
    label: string;
    exclusive_only: boolean;
    allowed: boolean;
    refusal: string | null;
  }>;
  pairing_codes: PairingCodePayload[];
}

export function invalidPayload(what: string, source: string): Error {
  return new Error(`Invalid ${what} from ${source}`);
}

function toFacts(facts: HostFactsPayload | null): HostFacts {
  return {
    cpus: facts?.cpus ?? null,
    memoryMib: facts?.memory_mib ?? null,
    diskFreeMib: facts?.disk_free_mib ?? null,
    kernel: facts?.kernel ?? null,
    arch: facts?.arch ?? null,
    cgroupV2: facts?.cgroup_v2 ?? null,
    gpus: facts?.gpus ?? null,
    os: facts?.os ?? null,
  };
}

export function toComputeHost(host: ComputeHostPayload): ComputeHost {
  return {
    id: host.id,
    name: host.name,
    tags: host.tags,
    state: host.state,
    health: host.health,
    service: host.service,
    removeWhenEmpty: host.remove_when_empty,
    execution: host.execution,
    runtime: host.runtime,
    connector: host.connector,
    connection: {
      kind: host.connection.kind,
      target: host.connection.target,
      connected: host.connection.connected,
      since: host.connection.since,
      lastError: host.connection.last_error,
    },
    slots: host.slots,
    sessions: host.sessions.map((session) => ({
      slot: session.slot,
      sessionId: session.session_id,
      state: session.state,
    })),
    slotsFree: host.slots_free,
    facts: toFacts(host.facts),
    suggestedSlots: host.suggested_slots,
    findings: host.findings,
    hostKeyFingerprint: host.host_key_fingerprint,
    registeredAt: host.registered_at,
    registeredBy: host.registered_by,
    lastCheckedAt: host.last_checked_at,
  };
}

export function toPairingCode(code: PairingCodePayload): PairingCode {
  return {
    id: code.id,
    execution: code.execution,
    connector: code.connector,
    tags: code.tags,
    slots: code.slots,
    expiresAt: code.expires_at,
    createdBy: code.created_by,
    consumedByHostId: code.consumed_by_host_id,
  };
}

export function toNewPairingCode(code: NewPairingCodePayload): NewPairingCode {
  return {
    ...toPairingCode(code),
    code: code.code,
    command: code.command,
    commandWget: code.command_wget,
    joinUrl: code.join_url,
    pin: code.pin,
  };
}

export function toComputeHostsOverview(
  payload: ComputeHostsPayload | undefined,
  source: string,
): ComputeHostsOverview {
  if (
    !payload ||
    !Array.isArray(payload.hosts) ||
    !Array.isArray(payload.pairing_codes) ||
    !Array.isArray(payload.connectors) ||
    !Array.isArray(payload.runtimes) ||
    !payload.summary
  ) {
    throw invalidPayload('compute hosts', source);
  }
  const { summary } = payload;
  const connectors: ComputeConnector[] = payload.connectors.map((connector) => ({
    bindingId: connector.binding_id,
    kind: connector.kind,
    label: connector.label,
  }));
  const runtimes: ComputeRuntime[] = payload.runtimes.map((runtime) => ({
    execution: runtime.execution,
    runtime: runtime.runtime,
    label: runtime.label,
    exclusiveOnly: runtime.exclusive_only,
    allowed: runtime.allowed,
    refusal: runtime.refusal,
  }));
  return {
    poolId: payload.pool_id,
    hosts: payload.hosts.map(toComputeHost),
    summary: {
      hosts: summary.hosts,
      byState: summary.by_state,
      slotsTotal: summary.slots_total,
      slotsFree: summary.slots_free,
      freeByTagSet: summary.free_by_tag_set,
      warmTargets: summary.warm_targets,
    },
    connectors,
    runtimes,
    pairingCodes: payload.pairing_codes.map(toPairingCode),
  };
}

export function pairingCodeBody(request: PairingCodeRequest) {
  return {
    execution: request.execution,
    connector: request.connector,
    tags: request.tags,
    slots: request.slots,
  };
}

/** One host answered by a PATCH or an action route. */
export function toComputeHostResponse(
  payload: ComputeHostPayload | undefined,
  source: string,
): ComputeHost {
  if (!payload || typeof payload.id !== 'string' || !Array.isArray(payload.sessions)) {
    throw invalidPayload('compute host', source);
  }
  return toComputeHost(payload);
}

export function toRemoveResult(
  payload: { removed: boolean; host_cleanup_command?: string | null } | undefined,
  source: string,
): RemoveHostResult {
  if (!payload || typeof payload.removed !== 'boolean') {
    throw invalidPayload('host removal', source);
  }
  return { removed: payload.removed, hostCleanupCommand: payload.host_cleanup_command ?? null };
}
