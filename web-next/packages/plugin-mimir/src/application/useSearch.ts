import { useState, useDeferredValue } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService, SearchMode } from '../ports';

export interface UseSearchReturn {
  query: string;
  mode: SearchMode;
  setQuery: (q: string) => void;
  setMode: (m: SearchMode) => void;
  results: import('../domain/page').SearchResult[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

export function useSearch(): UseSearchReturn {
  const [query, setQuery] = useState('architecture');
  const deferredQuery = useDeferredValue(query);
  const [mode, setMode] = useState<SearchMode>('hybrid');
  const service = useService<IMimirService>('mimir');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['mimir', 'search', deferredQuery, mode],
    queryFn: () => service.pages.search(deferredQuery, mode),
    enabled: deferredQuery.trim().length > 0,
  });

  return {
    query,
    mode,
    setQuery,
    setMode,
    results: data ?? [],
    isLoading: isLoading && deferredQuery.trim().length > 0,
    isError,
    error,
  };
}
