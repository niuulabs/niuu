import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ForgeProject, ForgeProjectChanges, IVolundrService } from '@niuulabs/plugin-volundr';
import { NEW_PROJECT, type ProjectChoice } from '../domain/projects';
import type { IRealmGovernanceService } from '../ports';
import { REALMS_QUERY_KEY, REALM_GOVERNANCE_SERVICE_KEY } from './useRealmGovernance';

export const PROJECTS_QUERY_KEY = ['volundr', 'projects'] as const;

export function useForgeProjects() {
  const volundr = useService<IVolundrService>('volundr');
  return useQuery({ queryKey: PROJECTS_QUERY_KEY, queryFn: () => volundr.getProjects() });
}

/** The project a choice names, creating it first when it is a new one. */
export async function resolveProjectChoice(
  volundr: IVolundrService,
  choice: ProjectChoice,
  fallbackName: string,
  instanceId?: string,
): Promise<string | null> {
  if (!choice.projectId) return null;
  if (choice.projectId !== NEW_PROJECT) return choice.projectId;
  const name = choice.newProjectName.trim() || fallbackName.trim();
  const project = await volundr.createProject(name, instanceId ? { instanceId } : undefined);
  return project.id;
}

export function useCreateProject() {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => volundr.createProject(name.trim()),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY }),
  });
}

export function useUpdateProject() {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ project, changes }: { project: ForgeProject; changes: ForgeProjectChanges }) =>
      volundr.updateProject(project, changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY }),
  });
}

/** Move a workspace into the chosen project, or out of its project. */
export function useSetRealmProject(slug: string, workspaceName: string) {
  const realms = useService<IRealmGovernanceService>(REALM_GOVERNANCE_SERVICE_KEY);
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (choice: ProjectChoice) => {
      const projectId = await resolveProjectChoice(volundr, choice, workspaceName);
      return realms.setRealmProject(slug, projectId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: REALMS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
    },
  });
}
