import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import {
  MEMORY_IMPORTS_SERVICE_KEY,
  type IMemoryImportsService,
  type ITrackerBrowserService,
} from '@niuulabs/plugin-ting';
import type { ForgeProject, IVolundrService } from '@niuulabs/plugin-volundr';

export const projectMemoryKey = (projectId: string) => ['realms', 'project-memory', projectId];

/** The memories the project also reads (its mounts). */
export function useProjectMemoryMounts(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...projectMemoryKey(project.id), 'mounts'],
    queryFn: () => volundr.getProjectMemoryMounts(project),
  });
}

/** The registered memories a project can attach. */
export function useRegisteredMemories() {
  const mimir = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['realms', 'registered-memories'],
    queryFn: async () => {
      const entries = (await mimir.mounts.listRegistryMounts?.()) ?? [];
      return entries.filter((entry) => entry.enabled && entry.lifecycle === 'registered');
    },
  });
}

/** The project's ticket imports; only its owners may read them. */
export function useMemoryImports(projectId: string, enabled: boolean) {
  const imports = useService<IMemoryImportsService>(MEMORY_IMPORTS_SERVICE_KEY);
  return useQuery({
    queryKey: [...projectMemoryKey(projectId), 'imports'],
    queryFn: () => imports.list(projectId),
    enabled,
  });
}

/** The tracker projects the person can see, to pick what to bring in. */
export function useTrackerProjects(enabled: boolean) {
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  return useQuery({
    queryKey: ['realms', 'tracker-projects'],
    queryFn: () => tracker.listProjects(),
    enabled,
  });
}

/** A mount name for a registry entry: lowercase letters, digits, '-' and '_'. */
export function mountNameFor(entryName: string): string {
  const name = entryName
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '');
  return (name || 'memory').slice(0, 63);
}

/** "decisions/, runbooks/" → ["decisions/", "runbooks/"]. */
export function parsePrefixes(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((prefix) => prefix.trim())
    .filter(Boolean);
}
