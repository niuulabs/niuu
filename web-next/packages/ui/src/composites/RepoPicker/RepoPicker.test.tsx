import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import {
  BranchSelect,
  findRepoByRef,
  getCommonBranches,
  RepoSelect,
  type RepoRecord,
} from './RepoPicker';

const REPOS: RepoRecord[] = [
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'volundr',
    cloneUrl: 'https://github.com/niuulabs/volundr',
    url: 'https://github.com/niuulabs/volundr',
    defaultBranch: 'main',
    branches: ['main', 'develop', 'feat/workflows'],
  },
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'ravn',
    cloneUrl: 'https://github.com/niuulabs/ravn',
    url: 'https://github.com/niuulabs/ravn',
    defaultBranch: 'main',
    branches: ['main', 'develop'],
  },
  {
    provider: 'forgejo',
    org: 'local',
    name: 'device-operator',
    cloneUrl: 'https://git.local/device-operator.git',
    url: 'https://git.local/device-operator',
    defaultBranch: 'trunk',
    branches: ['trunk'],
  },
];

describe('RepoPicker', () => {
  it('loads only selected repositories and reports branch failures', async () => {
    let resolve!: (branches: string[]) => void;
    const loadBranches = vi.fn().mockImplementationOnce(
      () =>
        new Promise<string[]>((done) => {
          resolve = done;
        }),
    );
    const { rerender } = render(
      <BranchSelect
        repos={REPOS}
        selectedRepos="niuulabs/volundr"
        value="main"
        onChange={() => {}}
        loadBranches={loadBranches}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent('Loading branches');
    expect(loadBranches).toHaveBeenCalledTimes(1);
    expect(loadBranches).toHaveBeenCalledWith(REPOS[0]!.cloneUrl);
    resolve(['main', 'release']);
    await screen.findByRole('option', { name: 'release' });
    loadBranches.mockRejectedValueOnce(new Error('GitLab unavailable'));
    rerender(
      <BranchSelect
        repos={REPOS}
        selectedRepos="niuulabs/ravn"
        value="main"
        onChange={() => {}}
        loadBranches={loadBranches}
      />,
    );
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('GitLab unavailable'));
    expect(screen.queryByRole('option', { name: 'release' })).not.toBeInTheDocument();
  });
  it('matches repos by clone url or slug', () => {
    expect(findRepoByRef(REPOS, 'https://github.com/niuulabs/volundr')?.name).toBe('volundr');
    expect(findRepoByRef(REPOS, 'https://github.com/niuulabs/ravn')?.name).toBe('ravn');
    expect(findRepoByRef(REPOS, 'niuulabs/ravn')?.name).toBe('ravn');
    expect(findRepoByRef(REPOS, 'missing/repo')).toBeUndefined();
  });

  it('computes common branches for multiple repos', () => {
    expect(getCommonBranches(REPOS, ['niuulabs/volundr', 'niuulabs/ravn'])).toEqual([
      'main',
      'develop',
    ]);
    expect(getCommonBranches(REPOS, 'local/device-operator')).toEqual(['trunk']);
    expect(getCommonBranches(REPOS, '')).toEqual([]);
    expect(getCommonBranches(REPOS, ['missing/repo'])).toEqual([]);
  });

  it('renders repo options using slug mode', () => {
    render(
      <RepoSelect
        repos={REPOS}
        value=""
        valueMode="slug"
        onChange={() => undefined}
        testId="repo-select"
      />,
    );

    const select = screen.getByTestId('repo-select') as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(select.innerHTML).toContain('niuulabs/volundr');
    expect(select.innerHTML).toContain('niuulabs/ravn');
    expect(select.innerHTML).toContain('forgejo');
  });

  it('omits excluded repo options', () => {
    render(
      <RepoSelect
        repos={REPOS}
        value=""
        valueMode="slug"
        excludedRepos={['niuulabs/volundr']}
        onChange={() => undefined}
        testId="repo-select"
      />,
    );

    const select = screen.getByTestId('repo-select') as HTMLSelectElement;
    expect(select.innerHTML).not.toContain('niuulabs/volundr');
    expect(select.innerHTML).toContain('niuulabs/ravn');
  });

  it('renders common branch options from selected repos', () => {
    render(
      <BranchSelect
        repos={REPOS}
        selectedRepos={['niuulabs/volundr', 'niuulabs/ravn']}
        value="main"
        onChange={() => undefined}
        testId="branch-select"
      />,
    );

    const select = screen.getByTestId('branch-select') as HTMLSelectElement;
    expect(select.innerHTML).toContain('main');
    expect(select.innerHTML).toContain('develop');
    expect(select.innerHTML).not.toContain('feat/workflows');
  });
});
