import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';

export function renderWithMimir(
  ui: React.ReactNode,
  service?: IMimirService,
  ctx: Partial<PluginCtx> = {},
): RenderResult {
  const svc = service ?? createMimirMockAdapter();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const pluginCtx: PluginCtx = {
    tweaks: {},
    setTweak: () => {},
    ...ctx,
  };
  return render(
    <QueryClientProvider client={client}>
      <PluginCtxProvider value={pluginCtx}>
        <ServicesProvider services={{ mimir: svc }}>{ui}</ServicesProvider>
      </PluginCtxProvider>
    </QueryClientProvider>,
  );
}
