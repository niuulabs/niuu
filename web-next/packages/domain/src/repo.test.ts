import { describe, expect, it } from 'vitest';
import { normalizeRepoCatalogResponse } from './repo';

describe('normalizeRepoCatalogResponse', () => {
  it('normalizes grouped shared repo payloads', () => {
    expect(
      normalizeRepoCatalogResponse({
        GitHub: [
          {
            provider: 'github',
            org: 'niuulabs',
            name: 'volundr',
            url: 'https://github.com/niuulabs/volundr',
            clone_url: 'https://github.com/niuulabs/volundr.git',
            default_branch: 'main',
            branches: ['main', 'feat/workflows'],
          },
        ],
      }),
    ).toEqual([
      {
        provider: 'github',
        org: 'niuulabs',
        name: 'volundr',
        cloneUrl: 'https://github.com/niuulabs/volundr.git',
        url: 'https://github.com/niuulabs/volundr',
        defaultBranch: 'main',
        branches: ['main', 'feat/workflows'],
      },
    ]);
  });

  it('passes through already-normalized repo records', () => {
    const repos = [
      {
        provider: 'github',
        org: 'niuulabs',
        name: 'tyr',
        cloneUrl: 'https://github.com/niuulabs/tyr.git',
        url: 'https://github.com/niuulabs/tyr',
        defaultBranch: 'main',
        branches: ['main'],
      },
    ];

    expect(normalizeRepoCatalogResponse(repos)).toEqual(repos);
  });
});
