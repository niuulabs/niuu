import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import {
  createMockOdinReviewService,
  createMockRealmGovernanceService,
  createMockValkyrieService,
} from '../adapters/mock';

export function wrapWithValkyrie(services: Record<string, unknown> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider
          services={{
            valkyrie: createMockValkyrieService(),
            'valkyrie.reviews': createMockOdinReviewService(),
            'valkyrie.realms': createMockRealmGovernanceService(),
            ...services,
          }}
        >
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}
