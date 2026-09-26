/**
 * Ingest mutation (URL/file) backing the Memory scene's "Add a source" form
 * (`ui/memory/ExplorePanel.tsx`'s `AddSourceForm`) — a thin wrapper over
 * `IPageStore.ingestUrl`/`ingestFile`, so the form owns only its own state.
 */
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { Source } from '../domain/source';

export type IngestPayload = { type: 'url'; url: string } | { type: 'file'; file: File };

export interface UseIngestSourceReturn {
  ingest: (payload: IngestPayload) => void;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data: Source | undefined;
  reset: () => void;
}

export function useIngestSource(onSuccess?: (source: Source) => void): UseIngestSourceReturn {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (payload: IngestPayload) =>
      payload.type === 'url'
        ? service.pages.ingestUrl(payload.url)
        : service.pages.ingestFile(payload.file),
    onSuccess: (source) => {
      queryClient.invalidateQueries({ queryKey: ['mimir', 'sources'] });
      queryClient.invalidateQueries({ queryKey: ['mimir', 'memory-graph'] });
      onSuccess?.(source);
    },
  });

  return {
    ingest: mutation.mutate,
    isPending: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
    data: mutation.data,
    reset: mutation.reset,
  };
}
