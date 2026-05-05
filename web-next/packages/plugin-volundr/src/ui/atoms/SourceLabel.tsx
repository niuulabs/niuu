export interface SessionSource {
  type: 'git' | 'local_mount';
  repo?: string;
  branch?: string;
  url?: string;
  path?: string;
}

export interface SourceLabelProps {
  source: SessionSource | null | undefined;
  short?: boolean;
  className?: string;
}

/** Git repo@branch label or local mount path */
export function SourceLabel({ source, short = false, className }: SourceLabelProps) {
  if (!source) {
    return (
      <span className="niuu-font-mono niuu-text-text-faint" data-testid="source-label">
        —
      </span>
    );
  }

  if (source.type === 'git' && source.repo) {
    const parts = source.repo.split('/');
    const org = parts.slice(0, -1).join('/');
    const repo = parts[parts.length - 1] ?? source.repo;

    return (
      <span
        className={`niuu-inline-flex niuu-items-center niuu-gap-0.5 niuu-font-mono niuu-text-xs niuu-text-text-secondary ${className ?? ''}`}
        data-testid="source-label"
        title={`${source.repo}@${source.branch ?? 'main'}`}
      >
        <span className="niuu-text-text-muted" aria-hidden>
          ❯
        </span>
        {short ? (
          <span>{repo}</span>
        ) : (
          <>
            <span className="niuu-text-text-faint">{org}/</span>
            <span>{repo}</span>
          </>
        )}
        <span className="niuu-text-text-faint">@</span>
        <span className="niuu-text-brand">{source.branch ?? 'main'}</span>
      </span>
    );
  }

  return (
    <span
      className={`niuu-inline-flex niuu-items-center niuu-gap-0.5 niuu-font-mono niuu-text-xs niuu-text-text-secondary ${className ?? ''}`}
      data-testid="source-label"
      title={source.path}
    >
      <span className="niuu-text-text-muted" aria-hidden>
        ⌂
      </span>
      <span>{source.path}</span>
    </span>
  );
}
