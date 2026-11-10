import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import {
  isSharedSession,
  type ForgeProject,
  type ProjectMember,
  type VolundrSession,
} from '../../models/volundr.model';

/** The query key of a project's members; the project page reads the same cache entry. */
export const projectMembersKey = (projectId: string) =>
  ['volundr', 'projects', projectId, 'members'] as const;

type SharedCandidate = Pick<VolundrSession, 'room' | 'ownerId' | 'coordination'>;

/** The projects that hold at least one session the caller shares without owning. */
export function sharedProjects(
  sessions: readonly SharedCandidate[],
  projects: readonly ForgeProject[],
): ForgeProject[] {
  const ids = new Set<string>();
  for (const session of sessions) {
    if (!isSharedSession(session) || !session.coordination) continue;
    ids.add(session.coordination.projectId);
  }
  return projects.filter((project) => ids.has(project.id));
}

/**
 * Who owns each shared session, by name. The room summary names its owner by id only, and a
 * person cannot be looked up outside a project, so a name is known where the session's project
 * lists its owner as a member. A session whose owner is not found has no entry: the list says
 * "Shared" without a name rather than showing an id.
 */
export function useSharedOwnerNames(
  sessions: readonly SharedCandidate[],
  projects: readonly ForgeProject[],
): ReadonlyMap<string, string> {
  const volundr = useService<IVolundrService>('volundr');
  const targets = useMemo(() => sharedProjects(sessions, projects), [sessions, projects]);
  return useQueries({
    queries: targets.map((project) => ({
      queryKey: projectMembersKey(project.id),
      queryFn: () => volundr.getProjectMembers(project),
      staleTime: 60_000,
    })),
    combine: nameOwners,
  });
}

/** A static function, so TanStack Query recomputes the map only when a members list changes. */
function nameOwners(
  results: ReadonlyArray<{ data: ProjectMember[] | undefined }>,
): ReadonlyMap<string, string> {
  const names = new Map<string, string>();
  for (const result of results) {
    for (const member of result.data ?? []) {
      const name = member.display_name || member.email;
      if (name) names.set(member.user_id, name);
    }
  }
  return names;
}
