import { describe, expect, it } from 'vitest';
import type { RealmSummary, ValkyrieDashboard } from '@niuulabs/plugin-valkyrie';
import type { ForgeProject } from '@niuulabs/plugin-volundr';
import { groupByProject, slugForResident, unclaimedResidents } from './projects';

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

const card = (slug: string, projectId: string | null, pending = 0) => ({
  realm: realm(slug, projectId),
  pendingReviews: Array.from({ length: pending }),
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

describe('unclaimedResidents', () => {
  const resident = (id: string, name: string, environmentId: string) => ({
    id,
    name,
    environmentId,
  });
  const dashboard = {
    valkyries: [
      resident('1', 'runa', 'env-k8s-valhalla'),
      resident('2', 'regin', 'env-k8s-valhalla'),
      resident('3', 'sigrun', 'env-k8s-ymir'),
    ],
  } as unknown as ValkyrieDashboard;

  it('lists residents no workspace keeps', () => {
    const left = unclaimedResidents(dashboard, [realm('runa', null)]);
    expect(left.map((entry) => entry.name)).toEqual(['regin', 'sigrun']);
  });

  it('stops listing a resident once a workspace is made around it', () => {
    const capitalised = {
      valkyries: [resident('1', 'Runa', 'env-k8s-valhalla')],
    } as unknown as ValkyrieDashboard;
    const slug = slugForResident(capitalised.valkyries[0]!);
    expect(unclaimedResidents(capitalised, [realm(slug, null)])).toEqual([]);
  });

  it('has nothing to list before the dashboard answers', () => {
    expect(unclaimedResidents(undefined, [])).toEqual([]);
  });
});

describe('slugForResident', () => {
  it('uses the resident name, which is how a workspace finds it again', () => {
    expect(slugForResident({ name: 'Regin' } as never)).toBe('regin');
  });

  it('refuses a name that cannot make a slug', () => {
    expect(() => slugForResident({ name: '---' } as never)).toThrow(/no name/);
  });
});
