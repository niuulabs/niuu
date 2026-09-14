import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockSetupService } from '../adapters/mock';
import type { ISetupService } from '../ports';
import { SETUP_SERVICE_KEY } from '../ui/hooks';

export interface RenderWithSetupOptions {
  service?: ISetupService | null;
}

export function renderWithSetup(
  ui: React.ReactNode,
  options: RenderWithSetupOptions = {},
): RenderResult & { client: QueryClient } {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const service =
    options.service === undefined ? createMockSetupService({ latencyMs: 0 }) : options.service;
  const services: Record<string, unknown> = {};
  if (service) services[SETUP_SERVICE_KEY] = service;
  const result = render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>{ui}</ServicesProvider>
    </QueryClientProvider>,
  );
  return Object.assign(result, { client });
}
