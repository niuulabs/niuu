import { describe, it, expect } from 'vitest';
import {
  buildSessionTree,
  coordinationOf,
  groupByProject,
  isCoordinator,
  leadState,
  nestSessions,
  unitSummary,
  unitSummaryLabel,
} from './projectTree';
import type { Session } from './session';

function session(id: string, projectId?: string, parent?: string): Session {
  return {
    id,
    clusterId: 'thor',
    startedAt: '2026-09-17T10:00:00Z',
    ...(projectId
      ? {
          coordination: {
            projectId,
            role: 'worker',
            parent: parent ? { instanceId: 'thor', sessionId: parent } : null,
          },
        }
      : {}),
  } as Session;
}
describe('project session hierarchy', () => {
  it('uses durable project ids, combines replicas and keeps unrelated sessions separate', () => {
    const sessions = [
      session('a', 'lexi'),
      session('b', 'lexi'),
      session('c'),
      session('d', 'unknown'),
    ];
    const groups = groupByProject(sessions, [
      { id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active' },
    ]);
    // A project the viewer is not on reads as shared, never as its raw id.
    expect(groups.map((group) => group.label)).toEqual([
      'Lexi',
      'Shared with you',
      'Independent sessions',
    ]);
    expect(groups[0]?.sessions.map((s) => s.id)).toEqual(['a', 'b']);
  });
  it('nests workers under their parent and promotes filtered or missing parents to the root', () => {
    const tree = buildSessionTree([
      session('worker', 'lexi', 'parent'),
      session('parent', 'lexi'),
      session('orphan', 'lexi', 'missing'),
    ]);
    expect(tree.map((node) => node.session.id)).toEqual(['parent', 'orphan']);
    expect(tree[0]?.children[0]?.session.id).toBe('worker');
    expect(buildSessionTree([session('worker', 'lexi', 'parent')])[0]?.session.id).toBe('worker');
  });
  it('breaks cycles and self references without losing sessions', () => {
    const tree = buildSessionTree([
      session('a', 'p', 'b'),
      session('b', 'p', 'a'),
      session('self', 'p', 'self'),
    ]);
    expect(tree.map((node) => node.session.id)).toEqual(['b', 'self']);
    expect(tree[0]?.children[0]?.session.id).toBe('a');
  });
  it('uses the host reference when session ids are ambiguous', () => {
    const tree = buildSessionTree([
      session('child', 'p', 'parent'),
      session('parent', 'p'),
      { ...session('parent', 'p'), clusterId: 'spark' },
    ]);
    expect(tree[0]?.children[0]?.session.id).toBe('child');
    expect(tree[1]?.children).toEqual([]);
  });
});

describe('nesting workers under coordinators', () => {
  const lead = session('lead', 'p');
  const w1 = { ...session('w1', 'p', 'lead'), state: 'running' } as Session;
  const w2 = { ...session('w2', 'p', 'lead'), state: 'idle' } as Session;
  const loner = session('loner', 'p');
  const ids = (sessions: Session[]) => sessions.map((s) => s.id);

  it('bucket roots only: a worker follows its coordinator, with the whole subtree behind it', () => {
    const nesting = nestSessions(
      [lead, w1, w2, loner],
      [lead, w1, w2, loner],
      [lead, w1, w2, loner],
    );
    expect(ids(nesting.roots).sort()).toEqual(['lead', 'loner']);
    expect(ids(nesting.subtree(lead)).sort()).toEqual(['lead', 'w1', 'w2']);
    expect(ids(nesting.subtree(lead))[0]).toBe('lead');
    expect(ids(nesting.subtree(loner))).toEqual(['loner']);
    expect(nesting.contextIds.size).toBe(0);
  });

  it('keeps a filtered-out coordinator as the path to a matching worker, flagged as context', () => {
    const nesting = nestSessions([w1], [lead, w1, w2, loner], [lead, w1, w2, loner]);
    expect(ids(nesting.roots)).toEqual(['lead']);
    expect(ids(nesting.subtree(lead)).sort()).toEqual(['lead', 'w1']);
    expect([...nesting.contextIds]).toEqual(['lead']);
  });

  it('keeps the whole path through a grandparent', () => {
    const top = session('top', 'p');
    const mid = session('mid', 'p', 'top');
    const leaf = session('leaf', 'p', 'mid');
    const nesting = nestSessions([leaf], [top, mid, leaf], [top, mid, leaf]);
    expect(ids(nesting.roots)).toEqual(['top']);
    expect(ids(nesting.subtree(top))).toEqual(['top', 'mid', 'leaf']);
    expect([...nesting.contextIds].sort()).toEqual(['mid', 'top']);
  });

  it('does not list a coordinator that is not a candidate (shown elsewhere, like the pinned group)', () => {
    const nesting = nestSessions([w1], [w1, w2], [lead, w1, w2]);
    expect(ids(nesting.roots)).toEqual(['w1']);
    expect(nesting.contextIds.size).toBe(0);
  });

  it('counts a coordinator’s workers among every known session, whatever the filters keep', () => {
    const nesting = nestSessions([lead], [lead, w1, w2], [lead, w1, w2, loner]);
    expect(nesting.workerCounts.get('lead')).toBe(2);
    expect(nesting.workerCounts.has('loner')).toBe(false);
    expect(ids(nesting.subtree(lead))).toEqual(['lead']);
  });

  it('refuses a subtree for a session that is not a root', () => {
    const nesting = nestSessions([lead, w1], [lead, w1], [lead, w1]);
    expect(() => nesting.subtree(w1)).toThrow('not a root');
  });

  it('is a coordinator by role or by having workers', () => {
    const counts = new Map([['lead', 2]]);
    expect(isCoordinator(lead, counts)).toBe(true);
    expect(isCoordinator(loner, counts)).toBe(false);
    const declared = {
      ...loner,
      coordination: { projectId: 'p', role: 'coordinator' },
    } as Session;
    expect(isCoordinator(declared, counts)).toBe(true);
  });

  it('reads who leads a session and whom it leads', () => {
    const all = [lead, w1, w2, loner];
    expect(ids(coordinationOf(all, 'lead').workers).sort()).toEqual(['w1', 'w2']);
    expect(coordinationOf(all, 'lead').coordinator).toBeNull();
    expect(coordinationOf(all, 'w1').coordinator?.id).toBe('lead');
    expect(coordinationOf(all, 'w1').workers).toEqual([]);
    expect(coordinationOf(all, 'missing')).toEqual({ coordinator: null, workers: [] });
  });
});

describe('a lead shows its workers when they are busier', () => {
  const withState = (base: Session, state: Session['state']): Session => ({ ...base, state });
  const lead = withState(session('root', 'run'), 'idle');

  it('reads as working while a worker works, and as needing you while one waits on you', () => {
    const idle = withState(session('coder', 'run', 'root'), 'idle');
    const working = withState(session('reviewer', 'run', 'root'), 'running');
    const waiting = withState(session('auditor', 'run', 'root'), 'awaiting_input');

    expect(leadState(lead, [lead, idle])).toBe('idle');
    expect(leadState(lead, [lead, idle, working])).toBe('running');
    expect(leadState(lead, [lead, working, waiting])).toBe('awaiting_input');
  });

  it("keeps a stopped or failed lead's own state, and ignores sessions it does not lead", () => {
    const working = withState(session('coder', 'run', 'root'), 'running');
    const elsewhere = withState(session('other', 'run', 'someone-else'), 'running');

    expect(leadState(withState(lead, 'terminated'), [working])).toBe('terminated');
    expect(leadState(withState(lead, 'failed'), [working])).toBe('failed');
    expect(leadState(lead, [lead, elsewhere])).toBe('idle');
  });
});

describe('sessions hosted in a unit', () => {
  const hosted = (id: string, hostedBy: string, extra: Partial<Session> = {}): Session =>
    ({ id, clusterId: 'thor', startedAt: '2026-09-17T10:00:00Z', hostedBy, ...extra }) as Session;
  const root = (id: string, extra: Partial<Session> = {}): Session =>
    ({ id, clusterId: 'thor', startedAt: '2026-09-17T10:00:00Z', ...extra }) as Session;

  it('nests a hosted session under its root without any project', () => {
    const tree = buildSessionTree([hosted('m1', 'team'), root('team'), hosted('m2', 'team')]);
    expect(tree.map((node) => node.session.id)).toEqual(['team']);
    expect(tree[0]?.children.map((node) => node.session.id).sort()).toEqual(['m1', 'm2']);
  });

  it('keeps a hosted session whose root is not listed, and picks the root on its own host', () => {
    expect(buildSessionTree([hosted('m1', 'missing')]).map((node) => node.session.id)).toEqual([
      'm1',
    ]);
    const tree = buildSessionTree([
      root('team', { clusterId: 'odin' }),
      root('team', { clusterId: 'thor' }),
      hosted('m1', 'team'),
    ]);
    const owner = tree.find((node) => node.children.length > 0);
    expect(owner?.session.clusterId).toBe('thor');
  });

  it('counts a root hosted members and shows its state as the busiest of them', () => {
    const team = root('team', { state: 'idle', team: { taskTo: null } });
    const members = [
      hosted('m1', 'team', { state: 'running' }),
      hosted('m2', 'team', { state: 'idle' }),
    ];
    expect(leadState(team, [team, ...members])).toBe('running');
    const nesting = nestSessions([team, ...members], [team, ...members], [team, ...members]);
    expect(nesting.workerCounts.get('team')).toBe(2);
    expect(nesting.roots.map((s) => s.id)).toEqual(['team']);
  });

  it('summarises a team, a session with people, and leaves a solo session alone', () => {
    const team = root('t', { team: { taskTo: 'lead' }, peopleCount: 2 });
    expect(unitSummary(team, 3)).toEqual({ agents: 3, people: 2 });
    expect(unitSummary(root('s', { peopleCount: 3 }), 0)).toEqual({ agents: 1, people: 3 });
    // A session that leads others says only who is in the room: whether it is an agent is its role.
    expect(unitSummary(root('c', { peopleCount: 2 }), 2)).toEqual({ agents: null, people: 2 });
    expect(unitSummary(root('solo', { peopleCount: 1 }), 0)).toBeNull();
    expect(unitSummary(root('legacy'), 0)).toBeNull();
  });

  it('words the summary for the owner, a participant and a crowd', () => {
    expect(unitSummaryLabel({ agents: 3, people: 2 }, true)).toBe('3 agents \u00b7 2 people');
    expect(unitSummaryLabel({ agents: 1, people: 2 }, false)).toBe('1 agent \u00b7 2 people');
    expect(unitSummaryLabel({ agents: 4, people: 1 }, true)).toBe('4 agents \u00b7 you');
    expect(unitSummaryLabel({ agents: 4, people: 1 }, false)).toBe('4 agents \u00b7 1 person');
    expect(unitSummaryLabel({ agents: null, people: 2 }, true)).toBe('2 people');
  });
});
