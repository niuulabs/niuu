import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import { ENTITY_KINDS } from '../domain/entity';
import type { EntityKind, EntityMeta } from '../domain/entity';

export interface UseEntitiesReturn {
  entities: EntityMeta[];
  grouped: Record<EntityKind, EntityMeta[]>;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

export function useEntities(kind?: EntityKind): UseEntitiesReturn {
  const service = useService<IMimirService>('mimir');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['mimir', 'entities', kind ?? null],
    queryFn: () => service.pages.listEntities(kind ? { kind } : undefined),
  });

  const entities = data ?? [];

  const grouped = ENTITY_KINDS.reduce(
    (acc, k) => {
      acc[k] = entities.filter((e) => e.entityKind === k);
      return acc;
    },
    {} as Record<EntityKind, EntityMeta[]>,
  );

  return { entities, grouped, isLoading, isError, error };
}
