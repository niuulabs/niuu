import type { RepoRecord } from '@niuulabs/domain';
import { useEffect, useState } from 'react';

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
  id?: string;
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
  id,
  placeholder = 'Select repository',
  excludedRepos = [],
  valueMode = 'cloneUrl',
  testId,
  className = '',
}: RepoSelectProps) {
  const groupedOptions = groupReposByProvider(repos, excludedRepos, valueMode);

  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      data-testid={testId}
      className={[
        'niuu-form-control',
        'niuu:w-full',
        'niuu:rounded-md',
        'niuu:border',
        'niuu:border-border-subtle',
        'niuu:bg-bg-primary',
        'niuu:px-3',
        'niuu:py-2',
        'niuu:text-sm',
        'niuu:text-text-primary',
        'outline-none',
        'niuu:focus:border-brand',
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
  loadBranches?: (repoUrl: string) => Promise<string[]>;
}

export function BranchSelect({
  repos,
  selectedRepos,
  value,
  onChange,
  placeholder = 'Select branch',
  testId,
  className = '',
  loadBranches,
}: BranchSelectProps) {
  const refs = Array.isArray(selectedRepos) ? selectedRepos : selectedRepos ? [selectedRepos] : [];
  const urls = JSON.stringify(refs.map((ref) => findRepoByRef(repos, ref)?.cloneUrl ?? ref));
  const [loaded, setLoaded] = useState<{ urls: string; branches: string[]; error?: string }>();
  useEffect(() => {
    if (!loadBranches) return;
    let cancelled = false;
    void (async () => {
      try {
        let common: string[] | undefined;
        // Only selected repositories are queried, sequentially for multi-repo pickers.
        for (const url of JSON.parse(urls) as string[]) {
          const branches = await loadBranches(url);
          if (cancelled) return;
          common = common === undefined ? branches : common.filter((b) => branches.includes(b));
        }
        if (!cancelled) setLoaded({ urls, branches: common ?? [] });
      } catch (error) {
        if (!cancelled) setLoaded({ urls, branches: [], error: String(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadBranches, urls]);
  const current = loaded?.urls === urls ? loaded : undefined;
  const options = loadBranches
    ? (current?.branches ?? [])
    : getCommonBranches(repos, selectedRepos);

  return (
    <div>
      {loadBranches && !current ? <p role="status">Loading branches…</p> : null}
      {current?.error ? <p role="alert">Could not load branches: {current.error}</p> : null}
      <select
        aria-label="Branch"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        data-testid={testId}
        className={[
          'niuu-form-control',
          'niuu:w-full',
          'niuu:rounded-md',
          'niuu:border',
          'niuu:border-border-subtle',
          'niuu:bg-bg-primary',
          'niuu:px-3',
          'niuu:py-2',
          'niuu:text-sm',
          'niuu:text-text-primary',
          'outline-none',
          'niuu:focus:border-brand',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
      >
        <option value="">{placeholder}</option>
        {value && !options.includes(value) ? <option value={value}>{value}</option> : null}
        {options.map((branch) => (
          <option key={branch} value={branch}>
            {branch}
          </option>
        ))}
      </select>
    </div>
  );
}
