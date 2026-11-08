import { ApiClientError, type ApiClient } from '@niuulabs/query';
import type { IRunLogService, ProjectSessions } from '../ports';
import type { Publication } from '../domain/artifacts';

interface PublicationRow {
  seq: number;
  session_id: string;
  provider_key: string;
  revision_id: string;
  tool_use_id: string;
  title: string | null;
  url: string | null;
  at: string;
  mime: string | null;
  version_label: string | null;
}

interface PublicationPage {
  items: PublicationRow[];
  next_after: number;
  has_more: boolean;
}

interface ForgeSession {
  id: string;
  name: string;
  status: string;
  tracker_issue_id?: string | null;
  coordination?: { role?: string | null } | null;
}

/** Ting tags each session of a workflow run `workflow:<run slug>`. */
const WORKFLOW_RUN_TAG = 'workflow:';

interface ForgeInstance {
  id: string;
}

/** Only a running session takes a message. */
const RUNNING = 'running';
const NOT_FOUND = 404;

interface ForgeProject {
  id: string;
  name: string;
  instance_id?: string | null;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

function toPublication(row: PublicationRow): Publication {
  return {
    seq: row.seq,
    sessionId: row.session_id,
    providerKey: row.provider_key,
    revisionId: row.revision_id,
    fileId: row.tool_use_id,
    title: row.title,
    url: row.url,
    at: row.at,
    mime: row.mime,
    versionLabel: row.version_label,
  };
}

/**
 * Reads what sessions presented through the Forge API: `client` is rooted at
 * `/api/v1/forge`, `registry` (the platform registry, `/api/v1/niuu`) lists the Forge
 * hosts. Without a registry there is one Forge: the one `client` talks to.
 */
export function buildRunLogHttpAdapter(
  client: ApiClient,
  registry: ApiClient | null = null,
): IRunLogService {
  return {
    async hosts() {
      if (!registry) return [null];
      const instances = await registry.get<ForgeInstance[]>(
        '/instances?kind=volundr&enabledOnly=true',
      );
      return instances.map((instance) => instance.id);
    },
    async session(sessionId, instanceId) {
      try {
        const session = await client.get<ForgeSession>(
          `/sessions/${encodeURIComponent(sessionId)}${query({ instance_id: instanceId })}`,
        );
        const tag = session.tracker_issue_id ?? '';
        return {
          sessionId: session.id,
          instanceId,
          title: session.name,
          running: session.status === RUNNING,
          ...(tag.startsWith(WORKFLOW_RUN_TAG)
            ? {
                runSlug: tag.slice(WORKFLOW_RUN_TAG.length),
                member: session.coordination?.role ?? '',
              }
            : {}),
        };
      } catch (error) {
        if (error instanceof ApiClientError && error.status === NOT_FOUND) return null;
        throw error;
      }
    },
    async publications(instanceId, options = {}) {
      const publications: Publication[] = [];
      let after = 0;
      // The feed pages until the server says it has no more; nothing here caps it.
      for (;;) {
        const page = await client.get<PublicationPage>(
          `/artifact-publications${query({
            source: 'present_file',
            session_id: options.sessionId,
            instance_id: instanceId,
            after,
          })}`,
          { signal: options.signal },
        );
        publications.push(...page.items.map(toPublication));
        if (!page.has_more) return publications;
        after = page.next_after;
      }
    },
    async presentedText(sessionId, instanceId, fileId, signal) {
      return client.get<string>(
        `/sessions/${encodeURIComponent(sessionId)}/files/presented/${encodeURIComponent(fileId)}${query({ instance_id: instanceId })}`,
        { signal },
      );
    },
    async projectSessions() {
      const projects = await client.get<ForgeProject[]>('/projects');
      return Promise.all(
        projects.map(async (project): Promise<ProjectSessions> => {
          const sessions = await client.get<{ id: string }[]>(
            `/sessions${query({ project_id: project.id, instance_id: project.instance_id })}`,
          );
          return {
            projectId: project.id,
            name: project.name,
            sessionIds: sessions.map((session) => session.id),
          };
        }),
      );
    },
    async sendMessage(sessionId, instanceId, content) {
      await client.post(
        `/sessions/${encodeURIComponent(sessionId)}/messages${query({ instance_id: instanceId })}`,
        { content },
      );
    },
  };
}
