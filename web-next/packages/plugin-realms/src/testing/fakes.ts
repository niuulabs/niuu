/**
 * Test doubles for the services the Realms plugin consumes. Every double records the
 * calls it received so tests can assert the recipe's order and payloads.
 */
import { createMimirMockAdapter, type IMimirService } from '@niuulabs/plugin-mimir';
import type {
  CreatedTrigger,
  DeployResidentRequest,
  IPersonaStore,
  IRavenStream,
  IResidentControl,
  ITriggerStore,
  PersonaCreateRequest,
  Ravn,
  Trigger,
} from '@niuulabs/plugin-ravn';
import type { ITrackerBrowserService } from '@niuulabs/plugin-ting';
import type {
  IRealmGovernanceService,
  RealmSummary,
  RealmTrustGrant,
} from '@niuulabs/plugin-valkyrie';
import { createMockVolundrService, type IVolundrService } from '@niuulabs/plugin-volundr';

export interface CallLog {
  calls: string[];
}

export function createCallLog(): CallLog {
  return { calls: [] };
}

export function fakeRealmService(log: CallLog, seed: RealmSummary[] = []): IRealmGovernanceService {
  const realms = [...seed];
  const grants = new Map<string, RealmTrustGrant[]>();
  return {
    async listRealms() {
      log.calls.push('listRealms');
      return realms;
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
        created_at: '2026-09-13T00:00:00Z',
        updated_at: '2026-09-13T00:00:00Z',
      };
      realms.push(realm);
      return realm;
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
    async listWorkflows() {
      return [];
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
  const rules = new Set<string>();
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
      async upsertRoutingRule(rule: { id: string; prefix: string; mountName: string }) {
        log.calls.push(`upsertRoutingRule:${rule.id}->${rule.mountName}`);
        rules.add(rule.id);
        return rule;
      },
      async deleteRoutingRule(id: string) {
        log.calls.push(`deleteRoutingRule:${id}`);
        if (!rules.delete(id)) {
          throw Object.assign(new Error(`Routing rule not found: ${id}`), { status: 404 });
        }
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

/** The shape the persona API hands back: every list and block present, like the real thing. */
function personaDetailOf(request: Partial<PersonaCreateRequest> & { name: string }) {
  return {
    role: 'build',
    letter: request.name.charAt(0).toUpperCase(),
    color: '',
    summary: '',
    description: '',
    systemPromptTemplate: '',
    allowedTools: [],
    forbiddenTools: [],
    permissionMode: 'default',
    iterationBudget: 20,
    ...request,
    isBuiltin: false,
    hasOverride: false,
    producesEvent: request.producesEventType ?? '',
    consumesEvents: request.consumesEvents ?? [],
    llm: {
      thinkingEnabled: request.llmThinkingEnabled ?? false,
      maxTokens: request.llmMaxTokens ?? 8192,
    },
    produces: { eventType: request.producesEventType ?? '', schemaDef: {} },
    consumes: { events: [], schemaDef: {} },
    yamlSource: '',
  } as never;
}

export function fakePersonas(log: CallLog): IPersonaStore {
  const personas = new Map<string, PersonaCreateRequest>();
  return {
    async listPersonas() {
      return [];
    },
    async getPersona(name) {
      const persona = personas.get(name);
      if (!persona) throw new Error(`Persona not found: ${name}`);
      return personaDetailOf(persona);
    },
    async getPersonaYaml() {
      return '';
    },
    async createPersona(request) {
      log.calls.push(`createPersona:${request.name}`);
      personas.set(request.name, request);
      return personaDetailOf(request);
    },
    async updatePersona(name, request) {
      log.calls.push(`updatePersona:${name}`);
      personas.set(name, request);
      return {
        ...request,
        isBuiltin: false,
        hasOverride: false,
        producesEvent: '',
        consumesEvents: [],
        llm: {},
        produces: {},
        consumes: {},
        yamlSource: '',
      } as never;
    },
    async deletePersona(name) {
      log.calls.push(`deletePersona:${name}`);
      if (!personas.delete(name)) {
        throw Object.assign(new Error(`Persona not found: ${name}`), { status: 404 });
      }
    },
    async forkPersona(name, request) {
      log.calls.push(`forkPersona:${name}->${request.newName}`);
      return { name: request.newName } as never;
    },
  };
}

export function fakeTriggers(
  log: CallLog,
  options: { executionEnabled?: boolean } = {},
): ITriggerStore {
  const { executionEnabled = true } = options;
  const triggers: Trigger[] = [];
  return {
    async listTriggers() {
      return triggers;
    },
    async createTrigger(request) {
      log.calls.push(`createTrigger:${request.kind}:${request.personaName}`);
      const trigger = {
        ...request,
        id: `00000000-0000-4000-8000-00000000000${triggers.length + 1}`,
        createdAt: '2026-09-13T00:00:00Z',
      } as Trigger;
      triggers.push(trigger);
      return { ...trigger, executionEnabled } as CreatedTrigger;
    },
    async deleteTrigger() {},
  };
}

export function fakeResidents(
  log: CallLog,
  options: { failDeploy?: boolean } = {},
): IResidentControl & IRavenStream {
  const ravens: Ravn[] = [];
  return {
    async listProfiles() {
      return [
        {
          id: 'profile-1',
          displayName: 'Valhalla resident',
          description: '',
          backend: 'helmrelease',
          engine: 'ravn',
          capabilities: ['chat', 'session.list', 'session.create', 'logs'],
          defaultModel: 'claude-fable-5',
          allowedModels: ['claude-fable-5'],
          labels: [],
          instanceId: 'inst-1',
          instanceName: 'valhalla',
          instanceSlug: 'valhalla',
        },
      ];
    },
    async deploy(request: DeployResidentRequest) {
      log.calls.push(`deploy:${request.name}:${request.personaName}`);
      if (options.failDeploy) throw new Error('profile is not enabled on this target');
      const ravn = {
        id: `ravn-${ravens.length + 1}`,
        personaName: request.personaName ?? '',
        residentName: request.name,
        status: 'active',
        model: request.model ?? 'claude-fable-5',
        createdAt: '2026-09-13T00:00:00Z',
        kind: 'resident',
        managed: true,
        instanceId: request.instanceId,
      } as unknown as Ravn;
      ravens.push(ravn);
      return ravn;
    },
    async applyLifecycle(ravn, action) {
      log.calls.push(`applyLifecycle:${ravn.residentName ?? ravn.id}:${action}`);
      return ravn;
    },
    async delete(ravn) {
      log.calls.push(`deleteResident:${ravn.residentName ?? ravn.id}`);
      const index = ravens.indexOf(ravn);
      if (index >= 0) ravens.splice(index, 1);
    },
    async getLogs() {
      return { entries: [], bufferTotal: 0 };
    },
    async listSessions() {
      return [];
    },
    async createSession() {
      throw new Error('not used in tests');
    },
    async deleteSession() {},
    async listRavens() {
      log.calls.push('listRavens');
      return ravens;
    },
    async getRaven(id) {
      const ravn = ravens.find((entry) => entry.id === id);
      if (!ravn) throw new Error(`Ravn not found: ${id}`);
      return ravn;
    },
  };
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
    async importProject(boardId, repos) {
      log.calls.push(`importProject:${boardId}:${repos.join(',')}`);
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
