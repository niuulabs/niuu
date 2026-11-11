/**
 * Compute hosts: machines registered as compute for one Forge.
 *
 * A Forge with `compute.hosts` configured lists them (`GET /api/v1/forge/compute/hosts`);
 * a Forge without answers 404 and has no Hosts view. Sessions go into free slots; hosts
 * are cleaned, never deleted. Everything here is pure: value types plus the arithmetic
 * the Hosts view shows.
 */

export type HostState =
  | 'pending'
  | 'ready'
  | 'in_service'
  | 'draining'
  | 'out_of_service'
  | 'quarantined'
  | 'unreachable'
  | 'removing';

export type HostHealth = 'pending' | 'healthy' | 'quarantined' | 'unreachable';
export type HostService = 'active' | 'draining' | 'out' | 'removing';
export type HostSessionState = 'starting' | 'busy' | 'failed' | 'draining';
export type TagMatch = 'all' | 'any';

/** What the join command installs on a host to undo itself; named in the removal copy. */
export const HOST_LEAVE_COMMAND = 'sudo niuu-host-leave';

/** How often the host list is re-read while the view is open. */
export const COMPUTE_HOSTS_POLL_MS = 5_000;
/** How often the list is re-read while a pairing code waits for its host to run. */
export const PAIRING_POLL_MS = 2_000;
const MS_PER_SECOND = 1_000;
const SECONDS_PER_MINUTE = 60;

export interface HostSession {
  slot: number;
  /** Null when the reader may not manage hosts: other people's session ids stay out. */
  sessionId: string | null;
  state: HostSessionState;
}

export interface HostConnection {
  kind: string;
  /** The host's address; empty when the reader may not manage hosts. */
  target: string;
  connected: boolean;
  since: string | null;
  lastError: string | null;
}

/** What the last check measured. A field the check did not measure is null. */
export interface HostFacts {
  cpus: number | null;
  memoryMib: number | null;
  diskFreeMib: number | null;
  kernel: string | null;
  arch: string | null;
  cgroupV2: boolean | null;
  gpus: number | null;
  os: string | null;
}

export interface SuggestedSlots {
  slots: number;
  /** The arithmetic behind the number, one line each. */
  lines: string[];
}

export interface ComputeHost {
  id: string;
  name: string;
  tags: string[];
  state: HostState;
  health: HostHealth;
  service: HostService;
  removeWhenEmpty: boolean;
  execution: string;
  runtime: string;
  connector: string;
  connection: HostConnection;
  /** Null until set: "set after first check". */
  slots: number | null;
  sessions: HostSession[];
  slotsFree: number;
  facts: HostFacts;
  suggestedSlots: SuggestedSlots | null;
  findings: string[];
  /** Null when the reader may not manage hosts. */
  hostKeyFingerprint: string | null;
  registeredAt: string;
  /** Null when the reader may not manage hosts. */
  registeredBy: string | null;
  lastCheckedAt: string | null;
}

export interface FreeByTagSet {
  tags: string[];
  free: number;
}

export interface WarmTarget {
  tags: string[];
  match: TagMatch;
  min: number;
  free: number;
  met: boolean;
}

export interface ComputeSummary {
  hosts: number;
  byState: Partial<Record<HostState, number>>;
  slotsTotal: number;
  slotsFree: number;
  freeByTagSet: FreeByTagSet[];
  warmTargets: WarmTarget[];
}

export interface ComputeConnector {
  bindingId: string;
  kind: string;
  label: string;
}

export interface ComputeRuntime {
  /** `id@revision`: the exact execution a host runs. */
  execution: string;
  runtime: string;
  label: string;
  exclusiveOnly: boolean;
  allowed: boolean;
  /** Why this runtime is not offered; set when `allowed` is false. */
  refusal: string | null;
}

export interface PairingCode {
  id: string;
  execution: string;
  connector: string;
  tags: string[];
  slots: number | null;
  expiresAt: string;
  createdBy: string;
  consumedByHostId: string | null;
}

export interface ComputeHostsOverview {
  poolId: string;
  /**
   * Whether the reader may change hosts. When false the page is read-only and Forge has
   * left the connectors, runtimes and pairing codes out of the answer.
   */
  canManage: boolean;
  hosts: ComputeHost[];
  summary: ComputeSummary;
  connectors: ComputeConnector[];
  runtimes: ComputeRuntime[];
  /** Unconsumed, unexpired and unrevoked. */
  pairingCodes: PairingCode[];
}

export interface PairingCodeRequest {
  execution: string;
  connector: string;
  tags: string[];
  /** Null leaves the number to the first check. */
  slots: number | null;
}

/**
 * The join script as a file, for an operator who reads it before running it: the same bytes
 * `join.sh` serves for the code, its SHA-256, and the commands that fetch, verify and run it.
 */
export interface JoinScript {
  filename: string;
  sha256: string;
  content: string;
  commands: string[];
}

/** A freshly minted code: the only time the code and its commands are returned. */
export interface NewPairingCode extends PairingCode {
  code: string;
  command: string;
  /** Null when the join is pinned: wget cannot check a pin, so only curl is offered. */
  commandWget: string | null;
  /** Where hosts reach this Forge's join routes. */
  joinUrl: string;
  /** The pinned certificate key (`sha256//…`) the command checks; null without a pin. */
  pin: string | null;
  script: JoinScript;
}

export interface ComputeHostChanges {
  tags?: string[];
  slots?: number;
  name?: string;
}

export interface RemoveHostResult {
  removed: boolean;
  /** Set when Forge could not clean the host itself: the one command to run there. */
  hostCleanupCommand: string | null;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export type HostTone = 'ok' | 'brand' | 'warn' | 'crit' | 'neutral';

const STATE_VIEW: Record<HostState, { label: string; tone: HostTone }> = {
  pending: { label: 'Joining', tone: 'neutral' },
  ready: { label: 'Ready', tone: 'ok' },
  in_service: { label: 'In service', tone: 'brand' },
  draining: { label: 'Draining', tone: 'warn' },
  out_of_service: { label: 'Out of service', tone: 'neutral' },
  quarantined: { label: 'Quarantined', tone: 'crit' },
  unreachable: { label: 'Disconnected', tone: 'crit' },
  removing: { label: 'Removing', tone: 'warn' },
};

/** A host Forge has reached but not finished preparing reads "Preparing", not "Joining". */
export function hostStateView(host: Pick<ComputeHost, 'state' | 'connection'>): {
  label: string;
  tone: HostTone;
} {
  if (host.state === 'pending' && host.connection.connected) {
    return { label: 'Preparing', tone: 'neutral' };
  }
  return STATE_VIEW[host.state];
}

/** Hosts that need an operator: a failed check or audit, or no connection. */
export function needsAttention(host: Pick<ComputeHost, 'state'>): boolean {
  return host.state === 'quarantined' || host.state === 'unreachable';
}

/** Quarantine is an error; a dropped connection recovers by itself. */
export function findingTone(host: Pick<ComputeHost, 'state'>): 'crit' | 'warn' {
  return host.state === 'quarantined' ? 'crit' : 'warn';
}

/** "3 in service · 1 draining · 1 joining · 2 need attention". */
export function hostsByStateLine(byState: ComputeSummary['byState']): string {
  const count = (state: HostState) => byState[state] ?? 0;
  const attention = count('quarantined') + count('unreachable');
  const parts: Array<[number, string]> = [
    [count('in_service'), 'in service'],
    [count('ready'), 'ready'],
    [count('draining'), 'draining'],
    [count('pending'), 'joining'],
    [count('out_of_service'), 'out of service'],
    [count('removing'), 'removing'],
    [attention, 'need attention'],
  ];
  return parts
    .filter(([n]) => n > 0)
    .map(([n, label]) => `${n} ${label}`)
    .join(' · ');
}

/** How far a warm target is below its minimum, which Forge counts as a health signal. */
export function warmShortfall(target: WarmTarget): number {
  return Math.max(0, target.min - target.free);
}

/** The remedy for a warm target that is short. */
export function warmRemedy(target: WarmTarget): string {
  if (!target.tags.length) return 'Register or resume a host.';
  return `Register or resume a host tagged ${target.tags.join(', ')}.`;
}

// ---------------------------------------------------------------------------
// Slots
// ---------------------------------------------------------------------------

export type SlotCell = 'busy' | 'starting' | 'failed' | 'free';

const SESSION_CELL: Record<HostSessionState, SlotCell> = {
  busy: 'busy',
  draining: 'busy',
  starting: 'starting',
  failed: 'failed',
};

/** One cell per slot: what holds it, in slot order. Empty until the slot count is set. */
export function slotCells(host: Pick<ComputeHost, 'slots' | 'sessions'>): SlotCell[] {
  if (host.slots === null) return [];
  const length = Math.max(host.slots, ...host.sessions.map((session) => session.slot + 1));
  const cells = Array.from({ length }, (): SlotCell => 'free');
  for (const session of host.sessions) cells[session.slot] = SESSION_CELL[session.state];
  return cells;
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

/** "12 used · 3 free of 15", with the reason a slot or the whole host is held when there is one. */
export function slotSummary(
  host: Pick<ComputeHost, 'slots' | 'sessions' | 'slotsFree' | 'state'>,
): string {
  if (host.slots === null) return 'set after first check';
  const used = host.sessions.length;
  const failed = host.sessions.filter((session) => session.state === 'failed');
  const base =
    host.slotsFree > 0
      ? `${used} used · ${host.slotsFree} free of ${host.slots}`
      : `${used} used of ${host.slots}`;
  if (failed.length) {
    return `${base} · ${failed.map((session) => `slot ${session.slot} not clean`).join(', ')}`;
  }
  if (host.state === 'draining') return `${plural(used, 'session')} left · no new ones`;
  if (host.state === 'unreachable' && used > 0) {
    return `${plural(used, 'session')} still running on the host`;
  }
  return base;
}

// ---------------------------------------------------------------------------
// Tags
// ---------------------------------------------------------------------------

/** Hosts whose tags satisfy the selector; no tags selects every host. */
export function filterHosts<T extends Pick<ComputeHost, 'tags'>>(
  hosts: readonly T[],
  tags: readonly string[],
  match: TagMatch,
): T[] {
  if (!tags.length) return [...hosts];
  return hosts.filter((host) => {
    const have = new Set(host.tags);
    return match === 'any' ? tags.some((tag) => have.has(tag)) : tags.every((tag) => have.has(tag));
  });
}

/** Distinct tags across hosts, sorted: what the selector suggests. */
export function hostTagOptions(hosts: readonly Pick<ComputeHost, 'tags'>[]): string[] {
  return [...new Set(hosts.flatMap((host) => host.tags))].sort((a, b) => a.localeCompare(b));
}

/** Tags typed as free text: comma or whitespace separated, without repeats. */
export function parseTagInput(value: string): string[] {
  return [...new Set(value.split(/[,\s]+/).filter(Boolean))];
}

// ---------------------------------------------------------------------------
// Pairing
// ---------------------------------------------------------------------------

/** Whole seconds until the code expires; 0 once it has. */
export function secondsUntil(expiresAt: string, now: number): number {
  const remaining = Math.ceil((Date.parse(expiresAt) - now) / MS_PER_SECOND);
  return Math.max(0, remaining);
}

/** "9:12". */
export function countdownLabel(seconds: number): string {
  const minutes = Math.floor(seconds / SECONDS_PER_MINUTE);
  const rest = seconds % SECONDS_PER_MINUTE;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

/** The command with the long code cut down for display; Copy still copies all of it. */
export function truncateCode(command: string, code: string, visible: number): string {
  if (code.length <= visible) return command;
  return command.replace(code, `${code.slice(0, visible)}…`);
}

/**
 * The host that joined with a code: the one host that was not there when the code was
 * minted and runs what the code asked for. The code itself is consumed on joining, so it
 * is gone from the list by the time the host shows.
 */
export function hostForCode(
  hosts: readonly ComputeHost[],
  known: ReadonlySet<string>,
  request: PairingCodeRequest,
): ComputeHost | undefined {
  const wanted = [...request.tags].sort().join('\n');
  return hosts.find(
    (host) =>
      !known.has(host.id) &&
      host.execution === request.execution &&
      host.connector === request.connector &&
      [...host.tags].sort().join('\n') === wanted,
  );
}

// ---------------------------------------------------------------------------
// Joining timeline
// ---------------------------------------------------------------------------

export type StepStatus = 'done' | 'now' | 'todo' | 'failed';

export interface JoinStep {
  id: 'joined' | 'connected' | 'preparing' | 'audit' | 'capacity' | 'ready';
  title: string;
  detail: string | null;
  status: StepStatus;
}

const READY_STATES: readonly HostState[] = ['ready', 'in_service', 'draining', 'out_of_service'];

/**
 * Where a joining host is: joined, connected with its key pinned, prepared by the
 * execution's recipe, audited, given a slot count, ready. Each step is derived from what
 * Forge reports about the host, so a step Forge cannot see yet stays "todo".
 */
export function joinSteps(host: ComputeHost): JoinStep[] {
  const connected = host.connection.connected;
  const prepared = host.health === 'healthy';
  const sized = host.slots !== null;

  let connectedStatus: StepStatus = 'now';
  if (connected) connectedStatus = 'done';
  else if (host.state === 'unreachable') connectedStatus = 'failed';

  let preparingStatus: StepStatus = 'now';
  if (prepared) preparingStatus = 'done';
  else if (!connected) preparingStatus = 'todo';
  else if (host.state === 'quarantined') preparingStatus = 'failed';

  let capacityStatus: StepStatus = 'todo';
  if (prepared) capacityStatus = sized ? 'done' : 'now';

  return [
    {
      id: 'joined',
      title: 'Joined',
      detail: 'The host key was generated on the host; only the public key was sent.',
      status: 'done',
    },
    {
      id: 'connected',
      title: 'Connected, key pinned',
      detail:
        connectedStatus === 'failed'
          ? (host.connection.lastError ?? 'Forge cannot reach the host.')
          : 'Forge reached the host and it matches the registered key.',
      status: connectedStatus,
    },
    {
      id: 'preparing',
      title: `Preparing · recipe ${host.execution}`,
      detail: null,
      status: preparingStatus,
    },
    {
      id: 'audit',
      title: 'Audit',
      detail:
        'Nothing left from any slot; nobody in sudo or docker; Nix trusted-users is root only.',
      status: prepared ? 'done' : 'todo',
    },
    { id: 'capacity', title: 'Set capacity', detail: null, status: capacityStatus },
    {
      id: 'ready',
      title: 'Ready',
      detail: null,
      status: READY_STATES.includes(host.state) ? 'done' : 'todo',
    },
  ];
}

/** A host waits for its capacity when it is healthy and has no slot count yet. */
export function needsCapacity(host: Pick<ComputeHost, 'health' | 'slots'>): boolean {
  return host.health === 'healthy' && host.slots === null;
}

// ---------------------------------------------------------------------------
// Row actions
// ---------------------------------------------------------------------------

export type HostAction =
  'open' | 'check' | 'drain' | 'drain_remove' | 'cancel_drain' | 'resume' | 'reprepare' | 'remove';

/**
 * What an operator may do to a host in its state. Remove and Re-prepare are only offered on
 * an empty host; a host with sessions leaves through drain and remove, which never stops
 * them, and is re-prepared once a drain has emptied it.
 */
export function hostActions(
  host: Pick<ComputeHost, 'state' | 'health' | 'slots' | 'sessions' | 'removeWhenEmpty'>,
): HostAction[] {
  const leave: HostAction[] =
    host.sessions.length === 0 ? ['drain', 'remove'] : ['drain', 'drain_remove'];
  const follow: HostAction[] = needsCapacity(host) || host.state === 'pending' ? ['open'] : [];
  const reprepare: HostAction[] = host.sessions.length === 0 ? ['reprepare'] : [];
  switch (host.state) {
    case 'pending':
      return [...follow, 'remove'];
    case 'ready':
      return [...follow, 'check', ...reprepare, ...leave];
    case 'in_service':
      return ['check', ...reprepare, 'drain', 'drain_remove'];
    case 'draining':
      return host.removeWhenEmpty ? ['check'] : ['check', 'cancel_drain', 'drain_remove'];
    case 'out_of_service':
      return ['check', ...reprepare, 'resume', 'remove'];
    case 'quarantined':
      return ['check', ...reprepare, ...leave];
    case 'unreachable':
      return ['check', ...leave];
    case 'removing':
      return [];
  }
}
