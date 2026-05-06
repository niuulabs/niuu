import { render, type RenderResult } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockTemplateStore,
  createMockSessionStore,
} from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';
import type { IClusterAdapter } from '../ports/IClusterAdapter';
import type { ITemplateStore } from '../ports/ITemplateStore';
import type { ISessionStore } from '../ports/ISessionStore';

export interface RenderWithVolundrOptions {
  service?: IVolundrService;
  clusterAdapter?: IClusterAdapter;
  templateStore?: ITemplateStore;
  sessionStore?: ISessionStore;
}

export function renderWithVolundr(
  ui: React.ReactNode,
  options: RenderWithVolundrOptions = {},
): RenderResult {
  const {
    service = createMockVolundrService(),
    clusterAdapter = createMockClusterAdapter(),
    templateStore = createMockTemplateStore(),
    sessionStore = createMockSessionStore(),
  } = options;

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const repoCatalog = {
    getRepos: async () => service.getRepos(),
    getBranches: async (repoUrl: string) => {
      const repos = await service.getRepos();
      const repo = repos.find(
        (item) =>
          item.cloneUrl === repoUrl ||
          item.url === repoUrl ||
          `${item.org}/${item.name}` === repoUrl,
      );
      return repo?.branches ?? ['main'];
    },
  };

  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          'niuu.repos': repoCatalog,
          volundr: service,
          'volundr.clusters': clusterAdapter,
          'volundr.templates': templateStore,
          'volundr.sessions': sessionStore,
          sessionStore,
        }}
      >
        {ui}
      </ServicesProvider>
    </QueryClientProvider>,
  );
}
