import type { ApiClient } from '@niuulabs/query';
import type { IRunLogService, ProjectSessions } from '../ports';
import type { RunOutcome } from '../domain/artifacts';

interface LogEntry {
  seq: number;
  kind: string;
  ts: string;
  payload: Record<string, unknown>;
}

interface LogPage {
  entries: LogEntry[];
  scanned_through: number;
  has_more: boolean;
}

interface ForgeProject {
  id: string;
  name: string;
  instance_id?: string | null;
}

function withInstance(path: string, instanceId: string | null, joiner = '?'): string {
  return instanceId ? `${path}${joiner}instance_id=${encodeURIComponent(instanceId)}` : path;
}

function toOutcome(entry: LogEntry): RunOutcome {
  const payload = entry.payload;
  const participant = (payload.participant ?? {}) as Record<string, unknown>;
  return {
    seq: entry.seq,
    at: entry.ts,
    eventType: String(payload.eventType ?? ''),
    participantId: String(payload.participantId ?? participant.peer_id ?? ''),
    participantType: String(participant.participant_type ?? ''),
    fields: (payload.fields ?? {}) as Record<string, unknown>,
  };
}

/** Reads runs through the Forge API: `client` is rooted at `/api/v1/forge`. */
export function buildRunLogHttpAdapter(client: ApiClient): IRunLogService {
  return {
    async outcomes(sessionId, instanceId, signal) {
      const outcomes: RunOutcome[] = [];
      let after = 0;
      // The log pages until the server says it has no more; nothing here caps it.
      for (;;) {
        const page = await client.get<LogPage>(
          withInstance(
            `/sessions/${encodeURIComponent(sessionId)}/log/page?after=${after}&show_internal=true`,
            instanceId,
            '&',
          ),
          { signal },
        );
        outcomes.push(
          ...page.entries.filter((entry) => entry.kind === 'room_outcome').map(toOutcome),
        );
        if (!page.has_more) return outcomes;
        after = page.scanned_through;
      }
    },
    async projectSessions() {
      const projects = await client.get<ForgeProject[]>('/projects');
      return Promise.all(
        projects.map(async (project): Promise<ProjectSessions> => {
          const sessions = await client.get<{ id: string }[]>(
            withInstance(
              `/sessions?project_id=${encodeURIComponent(project.id)}`,
              project.instance_id ?? null,
              '&',
            ),
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
        withInstance(`/sessions/${encodeURIComponent(sessionId)}/messages`, instanceId),
        { content },
      );
    },
  };
}
