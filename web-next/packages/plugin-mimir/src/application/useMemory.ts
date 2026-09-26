/**
 * Query hooks for the Simple-mode memory screens.
 *
 * Evidence and related pages are read per page path. Neither backend route
 * takes a mount, so both are keyed on the path alone — an adapter answers with
 * an empty list when the serving instance does not keep that page.
 */

import {
  keepPreviousData,
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { FactEvidence, ReviseRequest } from '../domain/evidence';
import type { Page, SearchResult } from '../domain/page';

export function evidenceKey(path: string) {
  return ['mimir', 'evidence', path] as const;
}

export function relatedKey(path: string, depth: number) {
  return ['mimir', 'related', path, depth] as const;
}

/** Proof counts for every Key Fact on one page. */
export function useEvidence(path: string | null) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: evidenceKey(path ?? ''),
    queryFn: () => service.pages.getEvidence(path!),
    enabled: path !== null && path.length > 0,
    placeholderData: keepPreviousData,
  });
}

/** Evidence for several pages at once, indexed by path. */
export function useEvidenceForPaths(paths: string[]): {
  byPath: Map<string, FactEvidence[]>;
  isLoading: boolean;
} {
  const service = useService<IMimirService>('mimir');
  const results = useQueries({
    queries: paths.map((path) => ({
      queryKey: evidenceKey(path),
      queryFn: () => service.pages.getEvidence(path),
      placeholderData: keepPreviousData,
    })),
  });
  const byPath = new Map<string, FactEvidence[]>();
  paths.forEach((path, index) => {
    const rows = results[index]?.data;
    if (rows) byPath.set(path, rows);
  });
  return { byPath, isLoading: results.some((result) => result.isLoading) };
}

/**
 * Search from the URL's question. Hybrid is the only mode Simple mode offers —
 * the backend runs keyword and meaning together regardless of what is asked.
 * The key matches the workbench's so both share one cache entry.
 */
export function useMemorySearch(query: string, mountName?: string) {
  const service = useService<IMimirService>('mimir');
  const trimmed = query.trim();
  return useQuery<SearchResult[]>({
    queryKey: ['mimir', 'search', trimmed, 'hybrid', mountName ?? null, false],
    queryFn: () => service.pages.search(trimmed, 'hybrid', mountName),
    enabled: trimmed.length > 0,
    placeholderData: keepPreviousData,
  });
}

/** Full pages for several paths at once, indexed by path. */
export function usePagesForPaths(paths: string[], mountName?: string): Map<string, Page> {
  const service = useService<IMimirService>('mimir');
  const results = useQueries({
    queries: paths.map((path) => ({
      queryKey: ['mimir', 'page', path, mountName ?? null],
      queryFn: () => service.pages.getPage(path, mountName),
      placeholderData: keepPreviousData,
    })),
  });
  const byPath = new Map<string, Page>();
  paths.forEach((path, index) => {
    const page = results[index]?.data;
    if (page) byPath.set(path, page);
  });
  return byPath;
}

/** The page's link-graph neighbourhood, `depth` hops out. */
export function useRelated(path: string | null, depth = 1) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: relatedKey(path ?? '', depth),
    queryFn: () => service.pages.getRelated(path!, depth),
    enabled: path !== null && path.length > 0,
    placeholderData: keepPreviousData,
  });
}

/**
 * Rewrite one fact. The evidence row is updated in place first so the fact
 * leaves the "worth a look" list immediately; a rejected write rolls that back
 * and surfaces the error at the form.
 */
export function useRevisePage() {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (request: ReviseRequest) => service.pages.revisePage(request),
    onMutate: async (request: ReviseRequest) => {
      const key = evidenceKey(request.path);
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<FactEvidence[]>(key);
      if (previous) {
        queryClient.setQueryData<FactEvidence[]>(
          key,
          previous.map((row) =>
            row.fact === request.oldFact
              ? {
                  ...row,
                  fact: request.newFact,
                  trend: 'stable',
                  proofCount: row.proofCount + 1,
                }
              : row,
          ),
        );
      }
      return { previous, key };
    },
    onError: (_error, _request, context) => {
      if (context?.previous) queryClient.setQueryData(context.key, context.previous);
    },
    onSettled: (_page, _error, request) => {
      void queryClient.invalidateQueries({ queryKey: evidenceKey(request.path) });
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'page', request.path] });
      void queryClient.invalidateQueries({ queryKey: ['mimir', 'recent-writes'] });
    },
  });
}
