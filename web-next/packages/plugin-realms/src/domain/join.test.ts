import { describe, expect, it } from 'vitest';
import type { ReviewItem as WorkReviewItem, Saga } from '@niuulabs/plugin-ting';
import type { VolundrSession } from '@niuulabs/plugin-volundr';
import type { RealmSummary, RealmTrustGrant } from './governance';
import {
  activeSessionCount,
  bindingFromGrants,
  buildRealmView,
  latestGrants,
  waitingOnBoard,
  workProjectForBoard,
  workspaceSessions,
} from './join';

const realm: RealmSummary = {
  id: 'r1',
  slug: 'lexi-api',
  name: 'Lexi API',
  sleipnir_domain: 'code',
  owner_id: null,
  instance_id: 'inst-1',
  autonomy_profile: 'balanced',
  created_at: '2026-09-13T00:00:00Z',
  updated_at: '2026-09-13T00:00:00Z',
};

const grants: RealmTrustGrant[] = [
  {
    id: 'g1',
    realm_id: 'r1',
    action_class: 'observe',
    target: 'niuulabs/lexi-api',
    level: 2,
    limits: {
      template: 'product-resident',
      tracker_board: 'board-1',
      branch: 'dev',
      mount_target: 'ymir',
    },
    granted_by: null,
    granted_at: '2026-09-13T00:00:00Z',
  },
  {
    id: 'g2',
    realm_id: 'r1',
    action_class: 'deploy',
    target: '*',
    level: 1,
    limits: {},
    granted_by: null,
    granted_at: '2026-09-13T00:00:00Z',
  },
  {
    id: 'g3',
    realm_id: 'r1',
    action_class: 'deploy',
    target: '*',
    level: 2,
    limits: {},
    granted_by: null,
    granted_at: '2026-09-13T00:01:00Z',
  },
];

const sessions = [
  { id: 's1', personaName: 'dev', status: 'running' },
  { id: 's2', personaName: 'dev', status: 'completed' },
  { id: 's3', personaName: 'dev', status: 'awaiting_input' },
] as unknown as VolundrSession[];

describe('realm joins', () => {
  it('finds the Work link of a tracker board', () => {
    const link = { id: 'link-1', trackerId: 'board-1' } as Saga;
    expect(workProjectForBoard([link], 'board-1')).toBe(link);
    expect(workProjectForBoard([link], 'board-2')).toBeNull();
    expect(workProjectForBoard([link], undefined)).toBeNull();
    expect(workProjectForBoard(undefined, 'board-1')).toBeNull();
  });

  it('counts what Work started for the board and everything that launched', () => {
    const member = (id: string, coordination: object) =>
      ({ id, personaName: '', status: 'running', coordination }) as unknown as VolundrSession;
    const all = [
      ...sessions,
      member('coordinator', { labels: ['work-project:link-1'] }),
      member('worker', { parent: { sessionId: 'coordinator', instanceId: 'x' } }),
      member('other-board', { labels: ['work-project:link-2'] }),
    ];
    expect(workspaceSessions(all, 'link-1').map((s) => s.id)).toEqual(['coordinator', 'worker']);
    expect(workspaceSessions(all, undefined)).toEqual([]);
    expect(workspaceSessions(undefined, 'link-1')).toEqual([]);
  });

  it('counts the sessions that are still going', () => {
    expect(activeSessionCount(sessions)).toBe(2);
  });

  it('keeps only what is waiting on the board', () => {
    const item = (taskId: string, state: string, projectId: string) =>
      ({ taskId, state, projectId }) as WorkReviewItem;
    const items = [
      item('here', 'waiting', 'link-1'),
      item('decided', 'done', 'link-1'),
      item('there', 'waiting', 'link-2'),
    ];
    expect(waitingOnBoard(items, 'link-1').map((entry) => entry.taskId)).toEqual(['here']);
    expect(waitingOnBoard(items, undefined)).toEqual([]);
    expect(waitingOnBoard(undefined, 'link-1')).toEqual([]);
  });

  it('reads the binding back from the observe grant', () => {
    expect(bindingFromGrants(grants)).toEqual({
      template: 'product-resident',
      repo: 'niuulabs/lexi-api',
      branch: 'dev',
      trackerBoard: 'board-1',
      bugBoard: undefined,
      mountTarget: 'ymir',
    });
    expect(bindingFromGrants([])).toBeNull();
  });

  it('keeps the latest grant per action class', () => {
    expect(latestGrants(grants)).toEqual([
      { actionClass: 'observe', level: 2, target: 'niuulabs/lexi-api' },
      { actionClass: 'deploy', level: 2, target: '*' },
    ]);
  });

  it('builds the realm view from every part', () => {
    const view = buildRealmView({ realm, grants });
    expect(view).toMatchObject({ slug: 'lexi-api', autonomyProfile: 'balanced' });
    expect(view.binding?.trackerBoard).toBe('board-1');
    expect(view.adopted).toBe(false);
  });

  it('marks a workspace with no memory target as having no memory mount of its own', () => {
    const boardOnly = grants.map((grant) =>
      grant.action_class === 'observe'
        ? { ...grant, limits: { tracker_board: 'board-1', mount_target: '' } }
        : grant,
    );
    const view = buildRealmView({ realm, grants: boardOnly });
    expect(view.adopted).toBe(true);
  });
});
