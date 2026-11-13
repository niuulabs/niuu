/**
 * Test doubles for the services the Realms plugin consumes. Every double records the
 * calls it received so tests can assert the recipe's order and payloads.
 */
import { createMimirMockAdapter, type IMimirService } from '@niuulabs/plugin-mimir';
import {
  createMockTingService,
  createMockWorkService,
  type ITrackerBrowserService,
  type ReviewItem as WorkReviewItem,
} from '@niuulabs/plugin-ting';
import { createMockVolundrService, type IVolundrService } from '@niuulabs/plugin-volundr';
import { createSeedRealms } from '../adapters/mock';
import { REALM_GOVERNANCE_SERVICE_KEY } from '../application/useRealmGovernance';
import type { RealmSummary, RealmTrustGrant } from '../domain/governance';
import type { IRealmGovernanceService } from '../ports';

export interface CallLog {
  calls: string[];
}

export function createCallLog(): CallLog {
  return { calls: [] };
}

export const WAITING_TICKET: WorkReviewItem = {
  taskId: 'run-1',
  kind: 'ticket',
  title: 'Write the spring lines',
  workflowName: 'Ticket Work Flow',
  state: 'waiting',
  gateLabel: 'Review the result',
  updatedAt: '2026-11-01T00:00:00Z',
  projectId: 'link-1',
  issueId: 'issue-1',
  identifier: 'NIU-1',
};

/** Overrides that put `slug` on a board with one ticket at a gate. */
export async function workspaceWithWaitingTicket(log: CallLog, slug: string) {
  const realms = fakeRealmService(log, createSeedRealms());
  await realms.createTrustGrant(slug, {
    action_class: 'observe',
    target: 'niuulabs/volundr',
    level: 2,
    limits: { tracker_board: 'board-1' },
  });
  return {
    [REALM_GOVERNANCE_SERVICE_KEY]: realms,
    ting: {
      ...createMockTingService(),
      getSagas: async () => [{ id: 'link-1', trackerId: 'board-1', name: 'Spring board' }],
    },
    'ting.work': {
      ...createMockWorkService(),
      reviews: async () => ({
        items: [
          WAITING_TICKET,
          { ...WAITING_TICKET, taskId: 'decided', state: 'done' },
          { ...WAITING_TICKET, taskId: 'elsewhere', projectId: 'link-2' },
        ],
      }),
    },
  };
}

export function fakeRealmService(log: CallLog, seed: RealmSummary[] = []): IRealmGovernanceService {
  const realms = [...seed];
  const grants = new Map<string, RealmTrustGrant[]>();
  return {
    subscribeRealmDetail() {
      return () => {};
    },
    async listRealms() {
      log.calls.push('listRealms');
      return [...realms];
    },
    async getRealm(slug) {
      const realm = realms.find((entry) => entry.slug === slug);
      if (!realm) throw new Error(`Realm not found: ${slug}`);
      return realm;
    },
    async createRealm(request) {
      log.calls.push(`createRealm:${request.slug}`);
      const realm: RealmSummary = {
        id: `realm-${realms.length + 1}`,
        slug: request.slug,
        name: request.name,
        sleipnir_domain: request.sleipnir_domain ?? null,
        owner_id: null,
        instance_id: request.instance_id ?? null,
        autonomy_profile: request.autonomy_profile ?? 'balanced',
        project_id: request.project_id ?? null,
        created_at: '2026-09-13T00:00:00Z',
        updated_at: '2026-09-13T00:00:00Z',
      };
      realms.push(realm);
      return realm;
    },
    async setRealmProject(slug, projectId) {
      log.calls.push(`setRealmProject:${slug}->${projectId}`);
      const index = realms.findIndex((entry) => entry.slug === slug);
      const realm = realms[index];
      if (!realm) throw new Error(`Realm not found: ${slug}`);
      realms[index] = { ...realm, project_id: projectId };
      return realms[index];
    },
    async deleteRealm(slug) {
      log.calls.push(`deleteRealm:${slug}`);
      const index = realms.findIndex((entry) => entry.slug === slug);
      if (index < 0) throw Object.assign(new Error(`Realm not found: ${slug}`), { status: 404 });
      realms.splice(index, 1);
      grants.delete(slug);
    },
    async listTrustGrants(slug) {
      log.calls.push(`listTrustGrants:${slug}`);
      return grants.get(slug) ?? [];
    },
    async createTrustGrant(slug, request) {
      log.calls.push(`createTrustGrant:${slug}:${request.action_class}:${request.level}`);
      const grant: RealmTrustGrant = {
        id: `g-${slug}-${(grants.get(slug) ?? []).length + 1}`,
        realm_id: slug,
        action_class: request.action_class,
        target: request.target,
        level: request.level,
        limits: request.limits,
        granted_by: null,
        granted_at: '2026-09-13T00:00:00Z',
      };
      grants.set(slug, [...(grants.get(slug) ?? []), grant]);
      return grant;
    },
  };
}

export function fakeMimir(
  log: CallLog,
  options: { mountAppears?: boolean; targets?: string[] } = {},
): IMimirService {
  const mountAppears = options.mountAppears ?? true;
  const targets = options.targets ?? ['ymir'];
  const mounts: string[] = [];
  // Reads the realm pages never touch come from the Mímir mock; writes are logged here.
  const base = createMimirMockAdapter();
  const mimir = {
    ...base,
    mounts: {
      ...base.mounts,
      async listMounts() {
        log.calls.push('listMounts');
        return mounts.map((name) => ({ name, role: 'domain', status: 'healthy', pages: 0 }));
      },
      async getDeployments() {
        log.calls.push('getDeployments');
        return {
          cluster: 'test',
          namespace: 'test',
          backends: ['mimir'],
          targets: targets.map((id) => ({
            id,
            cluster: 'test',
            namespace: 'test',
            backends: ['mimir'],
            releases: [],
          })),
          releases: [],
        };
      },
      async deployInstance(request: { name: string; target?: string }) {
        log.calls.push(`deployInstance:${request.name}@${request.target}`);
        // Mímir lists a deployed instance under its bare deployment name.
        if (mountAppears) mounts.push(request.name);
        return {};
      },
    },
    pages: {
      ...base.pages,
      async upsertPage(path: string, _content: string, mountName?: string) {
        log.calls.push(`upsertPage:${path}@${mountName}`);
      },
    },
  };
  return mimir as unknown as IMimirService;
}

export function fakeTracker(log: CallLog): ITrackerBrowserService {
  return {
    async listProjects() {
      return [
        {
          id: 'board-1',
          name: 'Lexi API',
          description: '',
          status: 'active',
          url: '',
          milestoneCount: 0,
          issueCount: 2,
          slug: 'LXA',
        },
      ];
    },
    async getProject(id) {
      return {
        id,
        name: 'Lexi API',
        description: '',
        status: 'active',
        url: '',
        milestoneCount: 0,
        issueCount: 2,
        slug: 'LXA',
      };
    },
    async listMilestones() {
      return [];
    },
    async listIssues(boardId) {
      log.calls.push(`listIssues:${boardId}`);
      return [
        {
          id: 'i1',
          identifier: 'LXA-1',
          title: 'Retry the webhook',
          description: '',
          status: 'todo',
          assignee: null,
          labels: [],
          priority: 2,
          url: '',
          milestoneId: null,
        },
      ];
    },
    async importProject(boardId, repos, _baseBranch, _instanceId, options) {
      const project = options?.forgeProjectId ? `@${options.forgeProjectId}` : '';
      log.calls.push(`importProject:${boardId}:${repos.join(',')}${project}`);
      return { id: 'saga-1', trackerId: boardId, repos } as never;
    },
  };
}

export function fakeVolundr(
  log: CallLog,
  options: { failIntegration?: string } = {},
): IVolundrService {
  const volundr = {
    // The launch wizard touches many more read methods when opened; the plugin's own
    // mock covers those, the overrides below carry the realm-specific answers.
    ...createMockVolundrService(),
    async getSessions() {
      return [];
    },
    async createProject(name: string) {
      log.calls.push(`createProject:${name}`);
      return { id: 'project-new', name, slug: 'new', status: 'active', revision: 1 };
    },
    async updateProject(project: { id: string }, changes: Record<string, unknown>) {
      log.calls.push(`updateProject:${project.id}:${Object.keys(changes).join(',')}`);
      return { ...project, ...changes };
    },
    async getRepos() {
      return [
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'lexi-api',
          cloneUrl: 'https://github.com/niuulabs/lexi-api.git',
          url: 'https://github.com/niuulabs/lexi-api',
          defaultBranch: 'dev',
          branches: ['dev', 'main'],
        },
      ];
    },
    async getIntegrations() {
      return [
        {
          id: 'int-1',
          slug: 'linear',
          integrationType: 'linear',
          enabled: true,
          createdAt: '',
          updatedAt: '',
        },
      ];
    },
    async testIntegration(id: string) {
      log.calls.push(`testIntegration:${id}`);
      if (options.failIntegration === id) return { success: false, error: 'token expired' };
      return { success: true };
    },
    async getAvailableMcpServers() {
      return [{ name: 'linear', type: 'http', url: 'https://mcp.example' }];
    },
  };
  return volundr as unknown as IVolundrService;
}
