/**
 * Query hooks for the memory screens.
 */

import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { Page, SearchResult } from '../domain/page';

/**
 * Search from the URL's question. Hybrid is the only mode these screens offer —
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
