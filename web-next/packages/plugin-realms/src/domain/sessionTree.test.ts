import { describe, expect, it } from 'vitest';
import type { VolundrSession } from '@niuulabs/plugin-volundr';
import { sessionRows, sessionsWithDescendants } from './sessionTree';

function session(id: string, parent?: string): VolundrSession {
  return {
    id,
    name: id,
    ...(parent ? { coordination: { parent: { sessionId: parent, instanceId: 'x' } } } : {}),
  } as unknown as VolundrSession;
}

describe('sessionsWithDescendants', () => {
  it('follows parent links down from the sessions named', () => {
    const all = [session('a'), session('b', 'a'), session('c', 'b'), session('other')];
    const found = sessionsWithDescendants(all, ['a']);
    expect(found.map((entry) => entry.id)).toEqual(['a', 'b', 'c']);
  });

  it('is empty before sessions load', () => {
    expect(sessionsWithDescendants(undefined, ['a'])).toEqual([]);
  });
});

describe('sessionRows', () => {
  it('nests each session under the one that started it', () => {
    const rows = sessionRows([session('child', 'root'), session('root'), session('leaf', 'child')]);
    expect(rows.map((row) => [row.session.id, row.depth])).toEqual([
      ['root', 0],
      ['child', 1],
      ['leaf', 2],
    ]);
  });

  it('makes a root of a session whose parent is not listed', () => {
    const rows = sessionRows([session('orphan', 'gone')]);
    expect(rows[0]?.depth).toBe(0);
  });

  it('still shows sessions caught in a cycle', () => {
    const rows = sessionRows([session('a', 'b'), session('b', 'a')]);
    expect(rows.map((row) => row.session.id).sort()).toEqual(['a', 'b']);
  });
});
