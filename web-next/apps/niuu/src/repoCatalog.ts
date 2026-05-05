import { normalizeRepoCatalogResponse, type RepoRecord } from '@niuulabs/domain';
import type { ApiClient } from '@niuulabs/query';

export interface RepoCatalogService {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
}

const MOCK_REPOS: RepoRecord[] = [
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'volundr',
    cloneUrl: 'https://github.com/niuulabs/volundr.git',
    url: 'https://github.com/niuulabs/volundr',
    defaultBranch: 'main',
    branches: ['main', 'feat/workflows'],
  },
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'web-next',
    cloneUrl: 'https://github.com/niuulabs/web-next.git',
    url: 'https://github.com/niuulabs/web-next',
    defaultBranch: 'main',
    branches: ['main'],
  },
];

export function buildRepoCatalogHttpAdapter(client: ApiClient): RepoCatalogService {
  return {
    getRepos: async () => normalizeRepoCatalogResponse(await client.get('/repos')),
    getBranches: async (repoUrl: string) =>
      client.get<string[]>(`/repos/branches?repo_url=${encodeURIComponent(repoUrl)}`),
  };
}

export function createMockRepoCatalogService(): RepoCatalogService {
  return {
    getRepos: async () => [...MOCK_REPOS],
    getBranches: async (repoUrl: string) => {
      const repo = MOCK_REPOS.find(
        (item) =>
          item.cloneUrl === repoUrl ||
          item.url === repoUrl ||
          `${item.org}/${item.name}` === repoUrl,
      );
      return repo?.branches ?? ['main'];
    },
  };
}
