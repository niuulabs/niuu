import { describe, it, expect } from 'vitest';
import { buildSessionTree, groupByProject } from './projectTree';
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
    expect(groups.map((group) => group.label)).toEqual(['Lexi', 'unknown', 'No project']);
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
