/** HTTP and mock adapters for Ting's memory imports (`{ting}/memory/imports`). */
import type { ApiClient } from '@niuulabs/query';
import type { IMemoryImportsService, MemoryImport } from '../ports';

interface RawImport {
  id: string;
  project_id: string;
  connector: 'linear';
  connection_id: string;
  tracker_project_id: string;
  compile: boolean;
  last_run_at: string | null;
  last_error: string;
}

function toImport(raw: RawImport): MemoryImport {
  return {
    id: raw.id,
    projectId: raw.project_id,
    connector: raw.connector,
    connectionId: raw.connection_id,
    trackerProjectId: raw.tracker_project_id,
    compile: raw.compile,
    lastRunAt: raw.last_run_at,
    lastError: raw.last_error,
  };
}

export function buildMemoryImportsHttpAdapter(client: ApiClient): IMemoryImportsService {
  return {
    async list(projectId) {
      const raw = await client.get<RawImport[]>(
        `/memory/imports?${new URLSearchParams({ project_id: projectId })}`,
      );
      return raw.map(toImport);
    },
    async create(input) {
      const raw = await client.post<RawImport>('/memory/imports', {
        project_id: input.projectId,
        connector: 'linear',
        connection_id: input.connectionId,
        tracker_project_id: input.trackerProjectId,
        compile: input.compile,
      });
      return toImport(raw);
    },
    async remove(importId) {
      await client.delete(`/memory/imports/${encodeURIComponent(importId)}`);
    },
  };
}

/** Imports kept in memory, for demos and tests. */
export function createMockMemoryImportsService(): IMemoryImportsService {
  let imports: MemoryImport[] = [];
  let next = 1;
  return {
    async list(projectId) {
      return imports.filter((item) => item.projectId === projectId).map((item) => ({ ...item }));
    },
    async create(input) {
      if (!input.trackerProjectId.trim()) throw new Error('tracker_project_id is required');
      const created: MemoryImport = {
        id: `import-${next++}`,
        projectId: input.projectId,
        connector: 'linear',
        connectionId: input.connectionId,
        trackerProjectId: input.trackerProjectId.trim(),
        compile: input.compile,
        lastRunAt: null,
        lastError: '',
      };
      imports = [...imports, created];
      return { ...created };
    },
    async remove(importId) {
      imports = imports.filter((item) => item.id !== importId);
    },
  };
}
