export {
  createApiClient,
  setTokenProvider,
  getAccessToken,
  ApiClientError,
  type ApiClient,
  type ApiError,
} from './http-client';

export { openEventStream, type EventStreamOptions, type EventStreamHandle } from './event-stream';

import { QueryClient, type QueryClientConfig } from '@tanstack/react-query';

export function createQueryClient(overrides: QueryClientConfig = {}): QueryClient {
  return new QueryClient({
    ...overrides,
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: 1,
        refetchOnWindowFocus: false,
        ...overrides.defaultOptions?.queries,
      },
      mutations: {
        retry: 0,
        ...overrides.defaultOptions?.mutations,
      },
    },
  });
}
