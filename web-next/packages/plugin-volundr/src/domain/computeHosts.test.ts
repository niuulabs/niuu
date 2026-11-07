import { describe, expect, it } from 'vitest';
import { seedComputeHosts } from '../adapters/computeHosts.mock';
import {
  countdownLabel,
  filterHosts,
  findingTone,
  hostActions,
  hostForCode,
  hostStateView,
  hostTagOptions,
  hostsByStateLine,
  joinSteps,
  needsAttention,
  needsCapacity,
  parseTagInput,
  secondsUntil,
  slotCells,
  slotSummary,
  truncateCode,
  warmRemedy,
  warmShortfall,
  type ComputeHost,
  type PairingCodeRequest,
} from './computeHosts';

const [hrimfaxi, skinfaxi, gloi, arvak, alsvin, ljosi, glad] = seedComputeHosts() as [
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
  ComputeHost,
];

describe('host state', () => {
  it('reads a pending host as joining until Forge reaches it, then preparing', () => {
    expect(
      hostStateView({ state: 'pending', connection: { ...glad.connection, connected: false } }),
    ).toEqual({ label: 'Joining', tone: 'neutral' });
    expect(hostStateView(glad)).toEqual({ label: 'Preparing', tone: 'neutral' });
  });

  it('labels the other states', () => {
    expect(hostStateView(hrimfaxi)).toEqual({ label: 'In service', tone: 'brand' });
    expect(hostStateView(arvak)).toEqual({ label: 'Draining', tone: 'warn' });
    expect(hostStateView(alsvin)).toEqual({ label: 'Quarantined', tone: 'crit' });
    expect(hostStateView(ljosi)).toEqual({ label: 'Disconnected', tone: 'crit' });
  });

  it('flags quarantined and unreachable hosts as needing attention', () => {
    expect(needsAttention(alsvin)).toBe(true);
    expect(needsAttention(ljosi)).toBe(true);
    expect(needsAttention(hrimfaxi)).toBe(false);
  });

  it('shows a quarantine as an error and a lost connection as a warning', () => {
    expect(findingTone(alsvin)).toBe('crit');
    expect(findingTone(ljosi)).toBe('warn');
  });

  it('counts hosts by state in words', () => {
    expect(
      hostsByStateLine({ in_service: 3, draining: 1, pending: 1, quarantined: 1, unreachable: 1 }),
    ).toBe('3 in service · 1 draining · 1 joining · 2 need attention');
    expect(hostsByStateLine({})).toBe('');
    expect(hostsByStateLine({ ready: 2, out_of_service: 1, removing: 1 })).toBe(
      '2 ready · 1 out of service · 1 removing',
    );
  });
});

describe('warm targets', () => {
  const short = { tags: ['gpu'], match: 'all' as const, min: 2, free: 0, met: false };

  it('measures the shortfall and never goes below zero', () => {
    expect(warmShortfall(short)).toBe(2);
    expect(warmShortfall({ ...short, free: 5, met: true })).toBe(0);
  });

  it('names the remedy by tag', () => {
    expect(warmRemedy(short)).toBe('Register or resume a host tagged gpu.');
    expect(warmRemedy({ ...short, tags: [] })).toBe('Register or resume a host.');
  });
});

describe('slots', () => {
  it('has one cell per slot, marked by what holds it', () => {
    const cells = slotCells(hrimfaxi);
    expect(cells).toHaveLength(15);
    expect(cells.filter((cell) => cell === 'busy')).toHaveLength(11);
    expect(cells[11]).toBe('starting');
    expect(cells[12]).toBe('free');
    expect(slotCells(alsvin)[4]).toBe('failed');
  });

  it('has no cells until the slot count is set', () => {
    expect(slotCells(glad)).toEqual([]);
  });

  it('keeps a session whose slot is past the count visible', () => {
    const cells = slotCells({
      slots: 1,
      sessions: [{ slot: 2, sessionId: 's', state: 'busy' }],
    });
    expect(cells).toEqual(['free', 'free', 'busy']);
  });

  it('counts a draining session as in use', () => {
    expect(
      slotCells({ slots: 1, sessions: [{ slot: 0, sessionId: 's', state: 'draining' }] }),
    ).toEqual(['busy']);
  });

  it('summarises a normal host', () => {
    expect(slotSummary(hrimfaxi)).toBe('12 used · 3 free of 15');
    expect(slotSummary(gloi)).toBe('1 used of 1');
  });

  it('says the count is not set yet', () => {
    expect(slotSummary(glad)).toBe('set after first check');
  });

  it('names a slot that is not clean', () => {
    expect(slotSummary(alsvin)).toBe('5 used · 3 free of 8 · slot 4 not clean');
  });

  it('says how many sessions a draining host has left', () => {
    expect(slotSummary(arvak)).toBe('2 sessions left · no new ones');
    expect(slotSummary({ ...arvak, sessions: arvak.sessions.slice(0, 1) })).toBe(
      '1 session left · no new ones',
    );
  });

  it('says sessions keep running on a host that lost its connection', () => {
    expect(slotSummary(ljosi)).toBe('2 sessions still running on the host');
    expect(slotSummary({ ...ljosi, sessions: ljosi.sessions.slice(0, 1) })).toBe(
      '1 session still running on the host',
    );
    expect(slotSummary({ ...ljosi, sessions: [], slotsFree: 4 })).toBe('0 used · 4 free of 4');
  });
});

describe('tags', () => {
  const hosts = [hrimfaxi, skinfaxi, gloi, ljosi];

  it('selects every host when no tag is chosen', () => {
    expect(filterHosts(hosts, [], 'all')).toHaveLength(4);
  });

  it('matches all of the chosen tags', () => {
    expect(filterHosts(hosts, ['site:hnoss', 'gpu'], 'all').map((host) => host.name)).toEqual([
      'gloi',
    ]);
  });

  it('matches any of the chosen tags', () => {
    expect(filterHosts(hosts, ['gpu', 'arm64'], 'any').map((host) => host.name)).toEqual([
      'gloi',
      'ljosi',
    ]);
  });

  it('lists the distinct tags, sorted', () => {
    expect(hostTagOptions(hosts)).toEqual(['arm64', 'fast-disk', 'gpu', 'site:hnoss', 'site:home']);
  });

  it('parses typed tags without blanks or repeats', () => {
    expect(parseTagInput(' gpu, site:a  gpu ,, ')).toEqual(['gpu', 'site:a']);
    expect(parseTagInput('')).toEqual([]);
  });
});

describe('pairing', () => {
  it('counts down to expiry and stops at zero', () => {
    const now = Date.parse('2026-11-06T12:00:00Z');
    expect(secondsUntil('2026-11-06T12:09:12Z', now)).toBe(552);
    expect(secondsUntil('2026-11-06T11:00:00Z', now)).toBe(0);
    expect(countdownLabel(552)).toBe('9:12');
    expect(countdownLabel(65)).toBe('1:05');
  });

  it('shortens the code in the command, not the command', () => {
    const command = "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz' x | sudo sh";
    expect(truncateCode(command, 'abcdefghijklmnopqrstuvwxyz', 6)).toBe(
      "curl -H 'Authorization: Bearer abcdef…' x | sudo sh",
    );
    expect(truncateCode(command, 'abc', 6)).toBe(command);
  });

  const request: PairingCodeRequest = {
    execution: 'host@2026-11-07',
    connector: 'direct-ssh',
    tags: ['fast-disk', 'site:hnoss'],
    slots: null,
  };

  it('finds the new host that runs what the code asked for', () => {
    const known = new Set([hrimfaxi.id, skinfaxi.id]);
    expect(hostForCode([hrimfaxi, skinfaxi, glad], known, request)?.name).toBe('glad');
  });

  it('ignores hosts that were there before and hosts with other settings', () => {
    const known = new Set([glad.id]);
    expect(hostForCode([glad], known, request)).toBeUndefined();
    expect(hostForCode([glad], new Set(), { ...request, tags: ['gpu'] })).toBeUndefined();
    expect(
      hostForCode([glad], new Set(), { ...request, connector: 'reverse-ssh' }),
    ).toBeUndefined();
    expect(hostForCode([glad], new Set(), { ...request, execution: 'other@1' })).toBeUndefined();
  });
});

describe('joining timeline', () => {
  const statuses = (host: ComputeHost) =>
    Object.fromEntries(joinSteps(host).map((step) => [step.id, step.status]));

  it('follows a host Forge has reached and is preparing', () => {
    expect(statuses(glad)).toEqual({
      joined: 'done',
      connected: 'done',
      preparing: 'now',
      audit: 'todo',
      capacity: 'todo',
      ready: 'todo',
    });
    expect(joinSteps(glad)[2]?.title).toBe('Preparing · recipe host@2026-11-07');
  });

  it('waits for the connection first', () => {
    const waiting = { ...glad, connection: { ...glad.connection, connected: false } };
    expect(statuses(waiting)).toMatchObject({ connected: 'now', preparing: 'todo' });
  });

  it('shows why Forge cannot reach the host', () => {
    const lost = {
      ...glad,
      state: 'unreachable' as const,
      connection: { ...glad.connection, connected: false, lastError: 'host key mismatch' },
    };
    const connected = joinSteps(lost)[1];
    expect(connected).toMatchObject({ status: 'failed', detail: 'host key mismatch' });
    expect(
      joinSteps({ ...lost, connection: { ...lost.connection, lastError: null } })[1]?.detail,
    ).toBe('Forge cannot reach the host.');
  });

  it('fails the preparing step of a quarantined host', () => {
    expect(statuses({ ...glad, state: 'quarantined' })).toMatchObject({ preparing: 'failed' });
  });

  it('asks for capacity once the host is healthy with no slot count', () => {
    const healthy = { ...glad, state: 'ready' as const, health: 'healthy' as const };
    expect(statuses(healthy)).toMatchObject({
      preparing: 'done',
      audit: 'done',
      capacity: 'now',
      ready: 'done',
    });
    expect(needsCapacity(healthy)).toBe(true);
    expect(needsCapacity({ ...healthy, slots: 4 })).toBe(false);
    expect(statuses({ ...healthy, slots: 4 })).toMatchObject({ capacity: 'done' });
  });
});

describe('row actions', () => {
  it('offers an in-service host check, drain and drain and remove', () => {
    expect(hostActions(hrimfaxi)).toEqual(['check', 'drain', 'drain_remove']);
  });

  it('lets a draining host be upgraded to a removal, once', () => {
    expect(hostActions({ ...arvak, removeWhenEmpty: false })).toEqual(['check', 'drain_remove']);
    expect(hostActions(arvak)).toEqual(['check']);
  });

  it('offers remove only on an empty host', () => {
    expect(hostActions(alsvin)).toEqual(['check', 'drain', 'drain_remove']);
    expect(hostActions({ ...alsvin, sessions: [] })).toEqual(['check', 'drain', 'remove']);
    expect(hostActions(ljosi)).toEqual(['check', 'drain', 'drain_remove']);
  });

  it('offers resume on an out-of-service host', () => {
    expect(hostActions({ ...skinfaxi, state: 'out_of_service', sessions: [] })).toEqual([
      'check',
      'resume',
      'remove',
    ]);
  });

  it('opens a joining host and a ready one that waits for its capacity', () => {
    expect(hostActions(glad)).toEqual(['open', 'remove']);
    const waiting = { ...glad, state: 'ready' as const, health: 'healthy' as const };
    expect(hostActions(waiting)).toEqual(['open', 'check', 'drain', 'remove']);
    expect(hostActions({ ...skinfaxi, state: 'ready', sessions: [] })).toEqual([
      'check',
      'drain',
      'remove',
    ]);
  });

  it('offers nothing on a host being removed', () => {
    expect(hostActions({ ...skinfaxi, state: 'removing' })).toEqual([]);
  });
});
