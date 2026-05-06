import type { RepoRecord } from '@niuulabs/domain';

export type { RepoRecord } from '@niuulabs/domain';

interface RepoOption {
  value: string;
  label: string;
}

interface RepoOptionGroup {
  label: string;
  options: RepoOption[];
}

const PROVIDER_LABELS: Record<string, string> = {
  github: 'GitHub',
  gitlab: 'GitLab',
  bitbucket: 'Bitbucket',
};

function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

function repoSlug(repo: RepoRecord): string {
  return `${repo.org}/${repo.name}`;
}

function repoValue(repo: RepoRecord, valueMode: 'cloneUrl' | 'slug'): string {
  return valueMode === 'slug' ? repoSlug(repo) : repo.cloneUrl;
}

export function findRepoByRef(repos: RepoRecord[], ref: string): RepoRecord | undefined {
  return repos.find((repo) => repo.cloneUrl === ref || repo.url === ref || repoSlug(repo) === ref);
}

export function groupReposByProvider(
  repos: RepoRecord[],
  excludedRepos: string[] = [],
  valueMode: 'cloneUrl' | 'slug' = 'cloneUrl',
): RepoOptionGroup[] {
  const excluded = new Set(excludedRepos);
  const groups = repos.reduce<Record<string, RepoOption[]>>((acc, repo) => {
    if (excluded.has(repoValue(repo, valueMode))) return acc;
    const key = providerLabel(repo.provider);
    acc[key] ??= [];
    acc[key].push({
      value: repoValue(repo, valueMode),
      label: repoSlug(repo),
    });
    return acc;
  }, {});

  return Object.entries(groups).map(([label, options]) => ({ label, options }));
}

export function getCommonBranches(repos: RepoRecord[], selectedRepos: string[] | string): string[] {
  const repoIds = Array.isArray(selectedRepos)
    ? selectedRepos
    : selectedRepos
      ? [selectedRepos]
      : [];
  if (repoIds.length === 0) return [];

  const resolved = repoIds
    .map((repoId) => findRepoByRef(repos, repoId))
    .filter((repo): repo is RepoRecord => Boolean(repo));

  if (resolved.length === 0) return [];

  return resolved.reduce<string[]>((acc, repo, index) => {
    if (index === 0) return [...repo.branches];
    return acc.filter((branch) => repo.branches.includes(branch));
  }, []);
}

export interface RepoSelectProps {
  repos: RepoRecord[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  excludedRepos?: string[];
  valueMode?: 'cloneUrl' | 'slug';
  testId?: string;
  className?: string;
}

export function RepoSelect({
  repos,
  value,
  onChange,
  placeholder = 'Select repository',
  excludedRepos = [],
  valueMode = 'cloneUrl',
  testId,
  className = '',
}: RepoSelectProps) {
  const groupedOptions = groupReposByProvider(repos, excludedRepos, valueMode);

  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      data-testid={testId}
      className={[
        'niuu-form-control',
        'niuu-w-full',
        'niuu-rounded-md',
        'niuu-border',
        'niuu-border-border-subtle',
        'niuu-bg-bg-primary',
        'niuu-px-3',
        'niuu-py-2',
        'niuu-text-sm',
        'niuu-text-text-primary',
        'outline-none',
        'focus:niuu-border-brand',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <option value="">{placeholder}</option>
      {groupedOptions.map((group) => (
        <optgroup key={group.label} label={group.label}>
          {group.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export interface BranchSelectProps {
  repos: RepoRecord[];
  selectedRepos: string[] | string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId?: string;
  className?: string;
}

export function BranchSelect({
  repos,
  selectedRepos,
  value,
  onChange,
  placeholder = 'Select branch',
  testId,
  className = '',
}: BranchSelectProps) {
  const options = getCommonBranches(repos, selectedRepos);

  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      data-testid={testId}
      className={[
        'niuu-form-control',
        'niuu-w-full',
        'niuu-rounded-md',
        'niuu-border',
        'niuu-border-border-subtle',
        'niuu-bg-bg-primary',
        'niuu-px-3',
        'niuu-py-2',
        'niuu-text-sm',
        'niuu-text-text-primary',
        'outline-none',
        'focus:niuu-border-brand',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <option value="">{placeholder}</option>
      {options.map((branch) => (
        <option key={branch} value={branch}>
          {branch}
        </option>
      ))}
    </select>
  );
}
