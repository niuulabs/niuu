import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  ForgeProject,
  IVolundrService,
  ProjectMember,
  ProjectMemberChange,
  ProjectRole,
} from '@niuulabs/plugin-volundr';

export const projectMembersKey = (projectId: string) =>
  ['volundr', 'projects', projectId, 'members'] as const;

/** Everyone on the project. */
export function useProjectMembers(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: projectMembersKey(project.id),
    queryFn: () => volundr.getProjectMembers(project),
  });
}

/** People of the project's org who are not on it yet, matching what the owner typed. */
export function useProjectMemberCandidates(project: ForgeProject, query: string, enabled: boolean) {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: [...projectMembersKey(project.id), 'candidates', query] as const,
    queryFn: () => volundr.getProjectMemberCandidates(project, query),
    enabled,
  });
}

/** The signed-in person, to know which member row is theirs. */
export function useViewer() {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({
    queryKey: ['volundr', 'identity'],
    queryFn: () => volundr.getIdentity(),
    staleTime: Infinity,
  });
}

/** What the viewer may do with the member list: manage it, or only edit their own profile. */
export function canManageMembers(
  members: readonly ProjectMember[],
  viewer: { userId: string; roles: readonly string[] } | undefined,
): boolean {
  if (!viewer) return false;
  if (viewer.roles.includes('volundr:admin')) return true;
  return members.some((member) => member.user_id === viewer.userId && member.role === 'owner');
}

function useInvalidateMembers(projectId: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: projectMembersKey(projectId) });
}

export function useAddProjectMember(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  const invalidate = useInvalidateMembers(project.id);
  return useMutation({
    mutationFn: (member: { userId: string; role: ProjectRole }) =>
      volundr.addProjectMember(project, member),
    onSuccess: invalidate,
  });
}

export function useUpdateProjectMember(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  const invalidate = useInvalidateMembers(project.id);
  return useMutation({
    mutationFn: ({ userId, change }: { userId: string; change: ProjectMemberChange }) =>
      volundr.updateProjectMember(project, userId, change),
    onSuccess: invalidate,
  });
}

export function useRemoveProjectMember(project: ForgeProject) {
  const volundr = useService<IVolundrService>('volundr');
  const invalidate = useInvalidateMembers(project.id);
  return useMutation({
    mutationFn: (userId: string) => volundr.removeProjectMember(project, userId),
    onSuccess: invalidate,
  });
}
