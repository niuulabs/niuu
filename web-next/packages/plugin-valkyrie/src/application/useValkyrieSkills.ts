import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IValkyrieSkillsService } from '../ports';

export const VALKYRIE_SKILLS_QUERY_KEY = ['valkyrie', 'learned-skills'] as const;

/** Learned skills adopted on one environment (canonical environment id). */
export function useValkyrieSkills(environmentId: string, enabled = true) {
  const service = useService<IValkyrieSkillsService>('valkyrie.skills');
  return useQuery({
    queryKey: [...VALKYRIE_SKILLS_QUERY_KEY, environmentId],
    queryFn: () => service.listSkills(environmentId),
    enabled: enabled && Boolean(environmentId),
  });
}

/** One skill's full record (markdown, tool code, tests); null when unknown. */
export function useValkyrieSkill(environmentId: string, name: string | null) {
  const service = useService<IValkyrieSkillsService>('valkyrie.skills');
  return useQuery({
    queryKey: [...VALKYRIE_SKILLS_QUERY_KEY, environmentId, name ?? ''],
    queryFn: () => service.getSkill(environmentId, name ?? ''),
    enabled: Boolean(environmentId && name),
  });
}
