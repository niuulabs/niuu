import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMemoryGatewayMockAdapter } from '../adapters/memoryGateway';
import { MEMORY_GATEWAY_SERVICE_KEY, type IMemoryGatewayService } from '../ports/IMemoryGateway';

/** Render one everyday memory page at `url`, with a memory gateway service. */
export function renderEveryday(
  Page: () => React.JSX.Element,
  url: string,
  gateway: IMemoryGatewayService = createMemoryGatewayMockAdapter(),
): RenderResult {
  const rootRoute = createRootRoute();
  const path = url.split('?')[0] ?? url;
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      createRoute({ getParentRoute: () => rootRoute, path, component: Page }),
    ]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ [MEMORY_GATEWAY_SERVICE_KEY]: gateway }}>
        <RouterProvider router={router} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}
