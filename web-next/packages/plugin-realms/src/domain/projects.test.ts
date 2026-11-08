import { describe, expect, it } from 'vitest';
import type { ForgeProject } from '@niuulabs/plugin-volundr';
import type { RealmSummary } from './governance';
import { groupByProject, projectsWithoutWorkspaces } from './projects';

function realm(slug: string, projectId: string | null): RealmSummary {
  return {
    id: slug,
    slug,
    name: slug,
    sleipnir_domain: null,
    owner_id: null,
    instance_id: null,
    autonomy_profile: 'balanced',
    project_id: projectId,
    created_at: '',
    updated_at: '',
  };
}

const card = (slug: string, projectId: string | null, waiting = 0) => ({
  realm: realm(slug, projectId),
  waitingTickets: Array.from({ length: waiting }, (_, index) => ({ index })),
});

const project = (id: string, name: string): ForgeProject => ({
  id,
  name,
  slug: id,
  status: 'active',
});

describe('groupByProject', () => {
  it('collects workspaces under their project', () => {
    const groups = groupByProject(
      [card('a', 'p1'), card('b', 'p1'), card('c', 'p2')],
      [project('p1', 'One'), project('p2', 'Two')],
    );
    expect(groups.map((group) => [group.name, group.cards.length])).toEqual([
      ['One', 2],
      ['Two', 1],
    ]);
  });

  it('puts what needs you first and the unfiled last', () => {
    const groups = groupByProject(
      [card('free', null, 9), card('a', 'p1'), card('b', 'p2', 1)],
      [project('p1', 'One'), project('p2', 'Two')],
    );
    expect(groups.map((group) => group.name)).toEqual(['Two', 'One', 'Not in a project']);
  });

  it('keeps a workspace whose project no Forge answered for', () => {
    const [group] = groupByProject([card('a', 'abcdefgh-1234')], []);
    expect(group?.name).toBe('Project abcdefgh');
    expect(group?.project).toBeNull();
  });
});

describe('projectsWithoutWorkspaces', () => {
  it('lists live projects no workspace serves, by name', () => {
    const empty = projectsWithoutWorkspaces(
      [card('a', 'p1')],
      [
        project('p3', 'Zebra'),
        project('p1', 'One'),
        project('p2', 'Alpha'),
        { ...project('p4', 'Old'), status: 'archived' },
      ],
    );
    expect(empty.map((entry) => entry.name)).toEqual(['Alpha', 'Zebra']);
  });
});
