import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IWorkService } from '../ports';
import type { WorkCollection, WorkResourceKind } from '../domain/work';

const EXECUTION_PAGE_SIZE = 50;

export function useWorkCollection() {
  const service = useService<IWorkService>('ting.work');
  return useInfiniteQuery({
    queryKey: ['ting', 'work'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      service.list({
        executionLimit: EXECUTION_PAGE_SIZE,
        ...(pageParam ? { executionCursor: pageParam } : {}),
      }),
    getNextPageParam: (page) => page.executionNextCursor ?? undefined,
  });
}

export function mergeWorkPages(pages: WorkCollection[] | undefined): WorkCollection | null {
  if (!pages?.length) return null;
  const first = pages[0];
  if (!first) return null;
  const latest = pages.at(-1) ?? first;
  return {
    projects: first.projects,
    campaigns: first.campaigns,
    executions: [
      ...new Map(
        pages.flatMap((page) => page.executions).map((item) => [item.id, item]),
      ).values(),
    ],
    executionNextCursor: latest.executionNextCursor,
    coverage: latest.coverage,
  };
}

export function useWorkDetail(kind: WorkResourceKind | null, id: string | null) {
  const service = useService<IWorkService>('ting.work');
  return useQuery({
    queryKey: ['ting', 'work', kind, id],
    queryFn: () => service.get(kind as WorkResourceKind, id as string),
    enabled: Boolean(kind && id),
  });
}
