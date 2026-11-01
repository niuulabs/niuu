import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  REALMS_QUERY_KEY,
  type IRealmGovernanceService,
  type ValkyrieResident,
} from '@niuulabs/plugin-valkyrie';
import type { ForgeProject, ForgeProjectChanges, IVolundrService } from '@niuulabs/plugin-volundr';
import { NEW_PROJECT, slugForResident, type ProjectChoice } from '../domain/projects';

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
  const realms = useService<IRealmGovernanceService>('valkyrie.realms');
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

export interface AdoptResidentRequest {
  resident: ValkyrieResident;
  name: string;
  choice: ProjectChoice;
}

/**
 * Give a resident that is already running a workspace. Nothing is deployed and the
 * resident is not touched: only the workspace record is created around it.
 */
export function useAdoptResident() {
  const realms = useService<IRealmGovernanceService>('valkyrie.realms');
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ resident, name, choice }: AdoptResidentRequest) => {
      const projectId = await resolveProjectChoice(volundr, choice, name);
      return realms.createRealm({
        slug: slugForResident(resident),
        name: name.trim(),
        project_id: projectId,
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: REALMS_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
    },
  });
}
