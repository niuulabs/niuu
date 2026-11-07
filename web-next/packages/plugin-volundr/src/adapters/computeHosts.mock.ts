/**
 * In-memory compute hosts for demos and tests: one Forge ("valhalla") with the hosts the
 * design mockup shows. It keeps the rules the real Forge enforces that the UI depends on:
 * slots change only on a host that is out of service or has none set, a host with
 * sessions cannot be removed, and a joined host shows up for the pairing code that made it.
 */
import { ApiClientError } from '@niuulabs/query';
import type {
  ComputeHost,
  ComputeHostChanges,
  ComputeHostsOverview,
  ComputeSummary,
  HostFacts,
  HostSession,
  HostState,
  NewPairingCode,
  PairingCode,
  PairingCodeRequest,
  RemoveHostResult,
} from '../domain/computeHosts';
import type { IVolundrService } from '../ports/IVolundrService';

export type MockComputeHostMethods = Pick<
  IVolundrService,
  | 'getComputeHosts'
  | 'getComputeHostTagSets'
  | 'createPairingCode'
  | 'revokePairingCode'
  | 'updateComputeHost'
  | 'drainComputeHost'
  | 'resumeComputeHost'
  | 'reprepareComputeHost'
  | 'checkComputeHost'
  | 'removeComputeHost'
>;

const MOCK_INSTANCE = 'valhalla';
const MOCK_EXECUTION = 'host@2026-11-07';
const MOCK_CONNECTOR = 'direct-ssh';
const MOCK_CODE_TTL_MS = 10 * 60 * 1000;
const MOCK_CLEANUP_COMMAND = 'sudo niuu-host-leave';
const MOCK_JOIN_URL = 'https://forge.example/api/v1/forge/compute/hosts/join.sh';
const MOCK_SCRIPT_FILENAME = 'niuu-join.sh';

/** A short stand-in for the script Forge renders for one code. */
function mockJoinScript(token: string): string {
  return [
    '#!/bin/sh',
    '# Join this machine to a Niuu Forge as compute (mock).',
    'set -eu',
    "FORGE_URL='https://forge.example'",
    `CODE='${token}'`,
    'echo "niuu: registering with $FORGE_URL"',
    '',
  ].join('\n');
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function facts(cpus: number, memoryGib: number, gpus = 0): HostFacts {
  return {
    cpus,
    memoryMib: memoryGib * 1024,
    diskFreeMib: 1_600_000,
    kernel: '6.8.0',
    arch: 'x86_64',
    cgroupV2: true,
    gpus,
    os: 'Ubuntu 24.04',
  };
}

function sessions(count: number, state: HostSession['state'] = 'busy'): HostSession[] {
  return Array.from({ length: count }, (_, slot) => ({
    slot,
    sessionId: `00000000-0000-4000-8000-${String(slot).padStart(12, '0')}`,
    state,
  }));
}

function host(overrides: Partial<ComputeHost> & Pick<ComputeHost, 'id' | 'name'>): ComputeHost {
  const held = overrides.sessions ?? [];
  const slots = overrides.slots === undefined ? 8 : overrides.slots;
  return {
    tags: ['site:hnoss'],
    state: 'in_service',
    health: 'healthy',
    service: 'active',
    removeWhenEmpty: false,
    execution: MOCK_EXECUTION,
    runtime: 'host',
    connector: MOCK_CONNECTOR,
    connection: {
      kind: 'direct_ssh',
      target: '192.168.90.30:22',
      connected: true,
      since: '2026-11-06T09:00:00Z',
      lastError: null,
    },
    slots,
    sessions: held,
    slotsFree: slots === null ? 0 : slots - held.length,
    facts: facts(32, 128),
    suggestedSlots: null,
    findings: [],
    hostKeyFingerprint: 'SHA256:Rr4aMockFingerprint0cX',
    registeredAt: '2026-11-01T10:00:00Z',
    registeredBy: 'jozef',
    lastCheckedAt: '2026-11-06T11:58:00Z',
    ...overrides,
  };
}

export function seedComputeHosts(): ComputeHost[] {
  return [
    host({
      id: 'host-hrimfaxi',
      name: 'hrimfaxi',
      tags: ['site:hnoss', 'fast-disk'],
      slots: 15,
      sessions: [...sessions(11), { slot: 11, sessionId: 'starting-1', state: 'starting' }],
      facts: facts(64, 256),
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.31:22',
        connected: true,
        since: '2026-11-06T09:00:00Z',
        lastError: null,
      },
    }),
    host({
      id: 'host-skinfaxi',
      name: 'skinfaxi',
      slots: 8,
      sessions: sessions(3),
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.32:22',
        connected: true,
        since: '2026-11-06T09:00:00Z',
        lastError: null,
      },
    }),
    host({
      id: 'host-gloi',
      name: 'gloi',
      tags: ['site:hnoss', 'gpu'],
      slots: 1,
      sessions: sessions(1),
      facts: facts(24, 96, 1),
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.36:22',
        connected: true,
        since: '2026-11-06T09:00:00Z',
        lastError: null,
      },
    }),
    host({
      id: 'host-arvak',
      name: 'arvak',
      state: 'draining',
      service: 'draining',
      removeWhenEmpty: true,
      slots: 8,
      sessions: sessions(2),
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.33:22',
        connected: true,
        since: '2026-11-06T09:00:00Z',
        lastError: null,
      },
    }),
    host({
      id: 'host-alsvin',
      name: 'alsvin',
      state: 'quarantined',
      health: 'quarantined',
      slots: 8,
      sessions: [...sessions(4), { slot: 4, sessionId: 'failed-1', state: 'failed' }],
      findings: [
        'node vite (pid 48211) still running as niuu-slot-4',
        '/dev/shm/vite-61004.lock owned by uid 61004',
      ],
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.34:22',
        connected: true,
        since: '2026-11-06T09:00:00Z',
        lastError: null,
      },
    }),
    host({
      id: 'host-ljosi',
      name: 'ljosi',
      tags: ['site:home', 'arm64'],
      state: 'unreachable',
      health: 'unreachable',
      slots: 4,
      sessions: sessions(2),
      facts: facts(20, 64),
      connector: 'reverse-ssh',
      connection: {
        kind: 'reverse_ssh',
        target: '',
        connected: false,
        since: '2026-11-06T11:51:00Z',
        lastError: 'connection reset',
      },
      findings: ['The outbound tunnel dropped (connection reset).'],
    }),
    host({
      id: 'host-glad',
      name: 'glad',
      tags: ['site:hnoss', 'fast-disk'],
      state: 'pending',
      health: 'pending',
      slots: null,
      facts: facts(48, 192),
      lastCheckedAt: null,
      registeredAt: '2026-11-06T11:57:00Z',
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.35:22',
        connected: true,
        since: '2026-11-06T11:57:00Z',
        lastError: null,
      },
    }),
    host({
      id: 'host-hofvarpnir',
      name: 'hofvarpnir',
      state: 'out_of_service',
      service: 'out',
      slots: 8,
      connection: {
        kind: 'direct_ssh',
        target: '192.168.90.37:22',
        connected: true,
        since: '2026-11-06T09:00:00Z',
        lastError: null,
      },
    }),
  ];
}

function summarize(hosts: ComputeHost[]): ComputeSummary {
  const byState: ComputeSummary['byState'] = {};
  for (const item of hosts) byState[item.state] = (byState[item.state] ?? 0) + 1;
  const serving = hosts.filter((item) => item.state === 'in_service');
  const tagSets = new Map<string, { tags: string[]; free: number }>();
  for (const item of serving) {
    const key = [...item.tags].sort().join('\n');
    const entry = tagSets.get(key) ?? { tags: [...item.tags].sort(), free: 0 };
    entry.free += item.slotsFree;
    tagSets.set(key, entry);
  }
  const free = serving.reduce((sum, item) => sum + item.slotsFree, 0);
  const gpuFree = serving
    .filter((item) => item.tags.includes('gpu'))
    .reduce((sum, item) => sum + item.slotsFree, 0);
  return {
    hosts: hosts.length,
    byState,
    slotsTotal: hosts.reduce((sum, item) => sum + (item.slots ?? 0), 0),
    slotsFree: free,
    freeByTagSet: [...tagSets.values()],
    warmTargets: [
      { tags: [], match: 'all', min: 4, free, met: free >= 4 },
      { tags: ['gpu'], match: 'all', min: 1, free: gpuFree, met: gpuFree >= 1 },
    ],
  };
}

function notFound(): ApiClientError {
  return new ApiClientError('API request failed: 404', 404, 'Host not found');
}

function conflict(detail: string): ApiClientError {
  return new ApiClientError('API request failed: 409', 409, detail);
}

export interface MockComputeHostsOptions {
  /** The Forge has no compute hosts: every read answers "none" (the real Forge answers 404). */
  unavailable?: boolean;
  hosts?: ComputeHost[];
  now?: () => number;
}

/** A stateful fake of the compute-hosts routes. */
export function createMockComputeHosts(
  options: MockComputeHostsOptions = {},
): MockComputeHostMethods {
  const hosts = new Map((options.hosts ?? seedComputeHosts()).map((item) => [item.id, item]));
  const codes = new Map<string, { code: PairingCode; request: PairingCodeRequest }>();
  const now = options.now ?? Date.now;
  let counter = 0;

  const hostOrThrow = (hostId: string): ComputeHost => {
    const found = hosts.get(hostId);
    if (!found) throw notFound();
    return found;
  };
  const save = (next: ComputeHost): ComputeHost => {
    hosts.set(next.id, next);
    return next;
  };
  const withState = (item: ComputeHost, state: HostState): ComputeHost => save({ ...item, state });

  return {
    getComputeHosts: async () => {
      if (options.unavailable) return null;
      const list = [...hosts.values()];
      const overview: ComputeHostsOverview = {
        poolId: `${MOCK_INSTANCE}-hosts`,
        hosts: list,
        summary: summarize(list),
        connectors: [
          { bindingId: 'direct-ssh', kind: 'direct_ssh', label: 'Forge connects over SSH' },
          { bindingId: 'reverse-ssh', kind: 'reverse_ssh', label: 'Host connects out' },
        ],
        runtimes: [
          {
            execution: MOCK_EXECUTION,
            runtime: 'host',
            label: 'Host · Nix + rootless Podman',
            exclusiveOnly: false,
            allowed: true,
            refusal: null,
          },
          {
            execution: 'openshell@2026-11-07',
            runtime: 'openshell',
            label: 'OpenShell',
            exclusiveOnly: true,
            allowed: true,
            refusal: null,
          },
          {
            execution: 'container@2026-11-07',
            runtime: 'container',
            label: 'Container (Docker)',
            exclusiveOnly: false,
            allowed: false,
            refusal: 'Needs root Docker, so not on registered hosts.',
          },
        ],
        pairingCodes: [...codes.values()]
          .map((entry) => entry.code)
          .filter((code) => Date.parse(code.expiresAt) > now()),
      };
      return overview;
    },
    getComputeHostTagSets: async () => {
      if (options.unavailable) return null;
      const sets = new Map<string, string[]>();
      for (const item of hosts.values()) {
        if (item.state !== 'in_service' || item.slots === null) continue;
        sets.set([...item.tags].sort().join('\n'), [...item.tags].sort());
      }
      return [...sets.values()];
    },
    createPairingCode: async (_instanceId, request): Promise<NewPairingCode> => {
      counter += 1;
      const code: PairingCode = {
        id: `code-${counter}`,
        execution: request.execution,
        connector: request.connector,
        tags: request.tags,
        slots: request.slots,
        expiresAt: new Date(now() + MOCK_CODE_TTL_MS).toISOString(),
        createdBy: 'jozef',
        consumedByHostId: null,
      };
      codes.set(code.id, { code, request });
      const token = `eyJhbGciOiJIUzI1NiJ9.mock-${counter}.signature`;
      const content = mockJoinScript(token);
      const sha256 = await sha256Hex(content);
      return {
        ...code,
        code: token,
        joinUrl: 'https://forge.example',
        pin: null,
        command: `curl -fsSL -H 'Authorization: Bearer ${token}' ${MOCK_JOIN_URL} | sudo sh`,
        commandWget: `wget -qO- --header='Authorization: Bearer ${token}' ${MOCK_JOIN_URL} | sudo sh`,
        script: {
          filename: MOCK_SCRIPT_FILENAME,
          sha256,
          content,
          commands: [
            `curl -fsSL -o ${MOCK_SCRIPT_FILENAME} -H 'Authorization: Bearer ${token}' ${MOCK_JOIN_URL}`,
            `echo '${sha256}  ${MOCK_SCRIPT_FILENAME}' | sha256sum -c -`,
            `less ${MOCK_SCRIPT_FILENAME}`,
            `sudo sh ${MOCK_SCRIPT_FILENAME}`,
          ],
        },
      };
    },
    revokePairingCode: async (_instanceId, codeId) => {
      if (!codes.delete(codeId)) throw notFound();
    },
    updateComputeHost: async (_instanceId, hostId, changes: ComputeHostChanges) => {
      const found = hostOrThrow(hostId);
      if (
        changes.slots !== undefined &&
        found.slots !== null &&
        found.state !== 'out_of_service' &&
        changes.slots !== found.slots
      ) {
        throw conflict('Changing slots needs the host out of service.');
      }
      const slots = changes.slots ?? found.slots;
      return save({
        ...found,
        name: changes.name ?? found.name,
        tags: changes.tags ?? found.tags,
        slots,
        slotsFree: slots === null ? 0 : slots - found.sessions.length,
      });
    },
    drainComputeHost: async (_instanceId, hostId, remove) => {
      const found = hostOrThrow(hostId);
      return save({
        ...found,
        state: 'draining',
        service: 'draining',
        removeWhenEmpty: remove,
      });
    },
    resumeComputeHost: async (_instanceId, hostId) => {
      const found = hostOrThrow(hostId);
      if (found.state === 'draining' && !found.removeWhenEmpty) {
        return withState(
          { ...found, service: 'active' },
          found.sessions.length ? 'in_service' : 'ready',
        );
      }
      if (found.state !== 'out_of_service') {
        throw conflict('Only a draining or out-of-service host can resume.');
      }
      return withState({ ...found, service: 'active' }, 'ready');
    },
    reprepareComputeHost: async (_instanceId, hostId) => {
      const found = hostOrThrow(hostId);
      if (found.sessions.length > 0) {
        throw conflict('Sessions run on this host; drain it, and re-prepare once it is empty.');
      }
      // The idle hold ends, so the next claim runs the current recipe; then it is checked.
      return withState(
        { ...found, lastCheckedAt: new Date(now()).toISOString() },
        found.state === 'in_service' ? 'ready' : found.state,
      );
    },
    checkComputeHost: async (_instanceId, hostId) =>
      save({ ...hostOrThrow(hostId), lastCheckedAt: new Date(now()).toISOString() }),
    removeComputeHost: async (_instanceId, hostId): Promise<RemoveHostResult> => {
      const found = hostOrThrow(hostId);
      if (found.sessions.length > 0) {
        throw conflict('Sessions still run on this host. Drain and remove it instead.');
      }
      hosts.delete(hostId);
      return {
        removed: true,
        hostCleanupCommand: found.connection.connected ? null : MOCK_CLEANUP_COMMAND,
      };
    },
  };
}
