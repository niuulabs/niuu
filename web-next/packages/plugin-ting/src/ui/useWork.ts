import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IWorkService } from '../ports';
import type { GateDecision, WorkCollection, WorkResourceKind } from '../domain/work';

const EXECUTION_PAGE_SIZE = 50;
/** A board follows runs it does not drive, so it refreshes while it is on screen. */
const BOARD_REFRESH_MS = 5000;
/** A review reads a run's whole session log, so it refreshes more slowly than the board. */
const REVIEW_REFRESH_MS = 15000;

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
      ...new Map(pages.flatMap((page) => page.executions).map((item) => [item.id, item])).values(),
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
    refetchInterval: (query) => (query.state.data?.board ? BOARD_REFRESH_MS : false),
  });
}

export function useReviews() {
  const service = useService<IWorkService>('ting.work');
  return useQuery({
    queryKey: ['ting', 'work', 'reviews'],
    queryFn: () => service.reviews(),
    refetchInterval: REVIEW_REFRESH_MS,
  });
}

export function useTicketReviews(projectId: string, issueId: string) {
  const service = useService<IWorkService>('ting.work');
  return useQuery({
    queryKey: ['ting', 'work', 'reviews', projectId, issueId],
    queryFn: () => service.ticketReviews(projectId, issueId),
    refetchInterval: REVIEW_REFRESH_MS,
  });
}

export function useGateDecision(projectId: string) {
  const service = useService<IWorkService>('ting.work');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, decision }: { issueId: string; decision: GateDecision }) =>
      service.decideGate(projectId, issueId, decision),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ting', 'work'] }),
  });
}

export function useStartTicket(projectId: string) {
  const service = useService<IWorkService>('ting.work');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, columnId }: { issueId: string; columnId?: string }) =>
      service.startTicket(projectId, issueId, columnId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ting', 'work'] }),
  });
}
