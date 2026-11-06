import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAuthHeaders } from '@niuulabs/query';
import { wsUrlToHttpBase } from '../chat';
import { cn } from '../utils/cn';
import { ReviewAddButton, ReviewLineThread } from './ReviewComments';
import type { ReviewControls } from './reviewModel';
import './SessionDiffs.css';

/** One changed file, as a session's broker lists it. */
export interface SessionDiffFile {
  path: string;
  status: 'new' | 'mod' | 'del';
  ins: number;
  del: number;
}

interface DiffHunkLine {
  type: 'context' | 'add' | 'remove';
  content: string;
  oldLine?: number;
  newLine?: number;
}

interface DiffHunk {
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: DiffHunkLine[];
}

interface DiffData {
  filePath: string;
  hunks: DiffHunk[];
}

type DiffBase = 'last-commit' | 'default-branch';

function authHeaders(): Record<string, string> {
  return Object.fromEntries(getAuthHeaders().entries());
}

function useLiveDiffViewer(chatEndpoint: string | null) {
  const apiBase = useMemo(
    () => (chatEndpoint ? wsUrlToHttpBase(chatEndpoint) : null),
    [chatEndpoint],
  );
  const mountedRef = useRef(true);
  const filesAbortRef = useRef<AbortController | null>(null);
  const diffAbortRef = useRef<AbortController | null>(null);
  const [files, setFiles] = useState<SessionDiffFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  // A failed load is said, never shown as an empty list that reads as "nothing changed".
  const [filesError, setFilesError] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffData | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<Error | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [diffBase, setDiffBaseState] = useState<DiffBase>('last-commit');

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      filesAbortRef.current?.abort();
      diffAbortRef.current?.abort();
    };
  }, []);

  const fetchFiles = useCallback(async () => {
    filesAbortRef.current?.abort();
    if (!apiBase) {
      if (mountedRef.current) {
        setFiles([]);
        setFilesLoading(false);
      }
      return;
    }
    const controller = new AbortController();
    filesAbortRef.current = controller;
    setFilesLoading(true);
    setFilesError(null);
    try {
      const params = new URLSearchParams({ base: diffBase });
      const response = await fetch(`${apiBase}/api/diff/files?${params}`, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch diff files: ${response.status}`);
      }
      const data = (await response.json()) as { files?: SessionDiffFile[] };
      if (!mountedRef.current || filesAbortRef.current !== controller) return;
      setFiles(data.files ?? []);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (!mountedRef.current || filesAbortRef.current !== controller) return;
      setFiles([]);
      setFilesError(
        `The changed files could not be loaded: ${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      if (mountedRef.current && filesAbortRef.current === controller) {
        filesAbortRef.current = null;
        setFilesLoading(false);
      }
    }
  }, [apiBase, diffBase]);

  const selectFile = useCallback(
    async (filePath: string) => {
      diffAbortRef.current?.abort();
      if (!apiBase) {
        setSelectedFile(filePath);
        setDiff(null);
        setDiffError(new Error('No session endpoint available'));
        return;
      }
      const controller = new AbortController();
      diffAbortRef.current = controller;
      setSelectedFile(filePath);
      setDiffLoading(true);
      setDiffError(null);
      try {
        const params = new URLSearchParams({ file: filePath, base: diffBase });
        const response = await fetch(`${apiBase}/api/diff?${params}`, {
          headers: authHeaders(),
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Failed to fetch diff: ${response.status}`);
        }
        if (!mountedRef.current || diffAbortRef.current !== controller) return;
        setDiff((await response.json()) as DiffData);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (!mountedRef.current || diffAbortRef.current !== controller) return;
        setDiff(null);
        setDiffError(error instanceof Error ? error : new Error('Failed to fetch diff'));
      } finally {
        if (mountedRef.current && diffAbortRef.current === controller) {
          diffAbortRef.current = null;
          setDiffLoading(false);
        }
      }
    },
    [apiBase, diffBase],
  );

  const setDiffBase = useCallback((base: DiffBase) => {
    setDiffBaseState(base);
    setSelectedFile(null);
    setDiff(null);
    setDiffError(null);
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchFiles();
    });
  }, [fetchFiles]);

  return {
    /** False when the session has no live endpoint, so its changes cannot be read now. */
    available: apiBase !== null,
    files,
    filesLoading,
    filesError,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  };
}

function diffStatusLetter(status: SessionDiffFile['status']): string {
  return status === 'new' ? 'A' : status === 'mod' ? 'M' : 'D';
}

function diffStatusColor(status: SessionDiffFile['status']): string {
  return status === 'new'
    ? 'niuu:text-state-ok'
    : status === 'mod'
      ? 'niuu:text-state-warn'
      : 'niuu:text-critical';
}

function DiffFileList({
  files,
  selectedPath,
  onSelect,
}: {
  files: SessionDiffFile[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
}) {
  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-auto"
      data-testid="diff-file-list"
    >
      <div className="niuu:border-b niuu:border-border-subtle niuu:bg-bg-primary niuu:px-4 niuu:py-3">
        <div className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-muted">
          changed files
        </div>
      </div>
      {files.map((f) => (
        <button
          key={f.path}
          type="button"
          onClick={() => onSelect(f.path)}
          className={cn(
            'niuu:flex niuu:items-center niuu:gap-2 niuu:border-b niuu:border-border-subtle niuu:px-4 niuu:py-2.5 niuu:text-left niuu:text-xs niuu:hover:bg-bg-elevated',
            selectedPath === f.path && 'niuu:bg-bg-elevated',
          )}
          data-testid={`diff-file-${f.status}`}
        >
          <span
            className={cn(
              'niuu:w-4 niuu:flex-shrink-0 niuu:font-mono niuu:font-medium',
              diffStatusColor(f.status),
            )}
          >
            {diffStatusLetter(f.status)}
          </span>
          <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:font-mono niuu:text-text-secondary">
            {f.path}
          </span>
          <span className="niuu:flex-shrink-0 niuu:font-mono niuu:text-[10px]">
            {f.ins > 0 && <span className="niuu:text-state-ok">+{f.ins}</span>}
            {f.del > 0 && <span className="niuu:ml-1 niuu:text-critical">-{f.del}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}

function DiffViewer({
  file,
  diff,
  loading,
  error,
  review,
}: {
  file: SessionDiffFile | null;
  diff: DiffData | null;
  loading: boolean;
  error: Error | null;
  review?: ReviewControls;
}) {
  const [composingLine, setComposingLine] = useState<string | null>(null);
  return (
    <div
      className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-auto"
      data-testid="diff-viewer"
    >
      <div className="niuu:flex niuu:flex-shrink-0 niuu:items-center niuu:gap-2 niuu:border-b niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3">
        {file ? (
          <>
            <span
              className={cn(
                'niuu:font-mono niuu:font-medium niuu:text-xs',
                diffStatusColor(file.status),
              )}
            >
              {diffStatusLetter(file.status)}
            </span>
            <span className="niuu:font-mono niuu:text-sm niuu:text-text-primary">{file.path}</span>
            <span className="niuu:font-mono niuu:text-xs niuu:text-text-muted">
              {file.ins > 0 && <span className="niuu:text-state-ok">+{file.ins}</span>}
              {file.del > 0 && <span className="niuu:ml-1 niuu:text-critical">-{file.del}</span>}
            </span>
          </>
        ) : (
          <span className="niuu:font-mono niuu:text-sm niuu:text-text-muted">Diff viewer</span>
        )}
      </div>
      <div className="niuu:flex-1 niuu:overflow-auto niuu:bg-bg-primary">
        {loading ? (
          <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:font-mono niuu:text-sm niuu:text-text-muted">
            Loading diff...
          </div>
        ) : error ? (
          <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:font-mono niuu:text-sm niuu:text-critical">
            Failed to load diff: {error.message}
          </div>
        ) : !file || !diff ? (
          <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:font-mono niuu:text-sm niuu:text-text-muted">
            Select a file to view changes
          </div>
        ) : diff.hunks.length === 0 ? (
          <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:font-mono niuu:text-sm niuu:text-text-muted">
            No changes in this file
          </div>
        ) : (
          diff.hunks.map((hunk, i) => (
            <div key={i} className="niuu:font-mono niuu:text-xs">
              <div className="niuu-diff-hunk-head niuu:bg-bg-tertiary niuu:px-4 niuu:py-0.5 niuu:text-text-muted">
                @@ -{hunk.oldStart},{hunk.oldCount} +{hunk.newStart},{hunk.newCount} @@
              </div>
              {hunk.lines.map((line, j) => {
                const side = line.type === 'remove' ? 'old' : 'new';
                const lineNumber = side === 'old' ? line.oldLine : line.newLine;
                const composerKey = `${side}:${lineNumber}`;
                return (
                  <Fragment key={j}>
                    <div
                      data-line={line.type}
                      className={cn(
                        'niuu-review-line niuu:flex niuu:gap-2 niuu:px-4 niuu:py-px',
                        line.type === 'add' &&
                          'niuu:bg-[color-mix(in_srgb,var(--color-brand)_8%,transparent)]',
                        line.type === 'remove' &&
                          'niuu:bg-[color-mix(in_srgb,var(--color-critical)_8%,transparent)]',
                      )}
                    >
                      {review && lineNumber != null ? (
                        <ReviewAddButton
                          line={lineNumber}
                          side={side}
                          onClick={() => setComposingLine(composerKey)}
                        />
                      ) : null}
                      <span className="niuu:w-8 niuu:flex-shrink-0 niuu:select-none niuu:text-right niuu:text-text-faint">
                        {line.oldLine ?? ''}
                      </span>
                      <span className="niuu:w-8 niuu:flex-shrink-0 niuu:select-none niuu:text-right niuu:text-text-faint">
                        {line.newLine ?? ''}
                      </span>
                      <span
                        className={cn(
                          'niuu:w-3 niuu:flex-shrink-0 niuu:select-none',
                          line.type === 'add' && 'niuu:text-state-ok',
                          line.type === 'remove' && 'niuu:text-critical',
                          line.type === 'context' && 'niuu:text-text-faint',
                        )}
                      >
                        {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
                      </span>
                      <span className="niuu:text-text-primary">{line.content || '\u00A0'}</span>
                    </div>
                    {review && lineNumber != null ? (
                      <ReviewLineThread
                        review={review}
                        filePath={file.path}
                        side={side}
                        line={lineNumber}
                        snippet={line.content}
                        composing={composingLine === composerKey}
                        onCloseComposer={() => setComposingLine(null)}
                      />
                    ) : null}
                  </Fragment>
                );
              })}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/** A session's changed files and their diffs, with line comments when review is given. */
export function SessionDiffs({
  chatEndpoint,
  review,
  layout = 'split',
}: {
  chatEndpoint: string | null;
  review?: ReviewControls;
  /**
   * `split`: the file list beside the diff. `dock`: Lexi's Changes pane for a narrow dock,
   * a summary line and one tab per file over the open file's diff.
   */
  layout?: 'split' | 'dock';
}) {
  const {
    available,
    files,
    filesLoading,
    filesError,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  } = useLiveDiffViewer(chatEndpoint);
  const selected = files.find((file) => file.path === selectedFile) ?? null;
  // The dock opens on the first changed file, as Lexi's Changes pane does.
  const firstFile = files[0]?.path;
  useEffect(() => {
    if (layout === 'dock' && firstFile && !selectedFile) void selectFile(firstFile);
  }, [layout, firstFile, selectedFile, selectFile]);

  if (layout === 'dock') {
    const added = files.reduce((total, file) => total + file.ins, 0);
    const removed = files.reduce((total, file) => total + file.del, 0);
    return (
      <div className="niuu-session-diffs niuu-session-diffs--dock" data-testid="diffs-tab">
        <div className="niuu-diffs-dock__head">
          <span className="niuu-diffs-dock__summary" data-testid="diff-summary">
            {files.length} {files.length === 1 ? 'file' : 'files'} changed
            {added > 0 ? <span className="niuu-diffs-dock__ins">+{added}</span> : null}
            {removed > 0 ? <span className="niuu-diffs-dock__del">−{removed}</span> : null}
          </span>
          <div className="niuu-diffs-dock__bases" role="group" aria-label="Compare with">
            {(['last-commit', 'default-branch'] as const).map((base) => (
              <button
                key={base}
                type="button"
                aria-pressed={diffBase === base}
                onClick={() => setDiffBase(base)}
              >
                {base === 'last-commit' ? 'Since last commit' : 'Against default branch'}
              </button>
            ))}
          </div>
        </div>
        {!available ? (
          <p className="niuu-diffs-dock__note" data-testid="diff-unavailable">
            Changes show while the session&apos;s workspace is live.
          </p>
        ) : filesLoading ? (
          <p className="niuu-diffs-dock__note">Loading changes…</p>
        ) : filesError ? (
          <p role="alert" className="niuu-diffs-dock__error" data-testid="diff-files-error">
            {filesError}
          </p>
        ) : files.length === 0 ? (
          <p className="niuu-diffs-dock__note" data-testid="diff-files-empty">
            {diffBase === 'last-commit'
              ? 'No changes since the last commit.'
              : 'No changes against the default branch.'}
          </p>
        ) : (
          <>
            <div className="niuu-diffs-dock__files" role="tablist" aria-label="Changed files">
              {files.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  role="tab"
                  title={file.path}
                  aria-selected={file.path === selectedFile}
                  data-testid={`diff-file-${file.status}`}
                  onClick={() => void selectFile(file.path)}
                >
                  <span className="niuu-diffs-dock__file-name">
                    {file.path.split('/').at(-1) ?? file.path}
                  </span>
                  {file.ins > 0 ? <span className="niuu-diffs-dock__ins">+{file.ins}</span> : null}
                  {file.del > 0 ? <span className="niuu-diffs-dock__del">−{file.del}</span> : null}
                </button>
              ))}
            </div>
            <div className="niuu-diffs-dock__viewer">
              <DiffViewer
                key={selected?.path}
                file={selected}
                diff={diff}
                loading={diffLoading}
                error={diffError}
                review={review}
              />
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div className="niuu-session-diffs niuu:h-full" data-testid="diffs-tab">
      <div className="niuu:overflow-hidden niuu:border-r niuu:border-border-subtle niuu:bg-bg-secondary">
        <div className="niuu:flex niuu:flex-shrink-0 niuu:items-center niuu:justify-between niuu:border-b niuu:border-border-subtle niuu:bg-bg-primary niuu:px-4 niuu:py-3">
          <div className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-muted">
            changed files
          </div>
          <div className="niuu:flex niuu:items-center niuu:gap-1">
            {(['last-commit', 'default-branch'] as const).map((base) => (
              <button
                key={base}
                type="button"
                onClick={() => setDiffBase(base)}
                className={cn(
                  'niuu:rounded-sm niuu:border niuu:px-2 niuu:py-1 niuu:font-mono niuu:text-[10px]',
                  diffBase === base
                    ? 'niuu:border-brand niuu:bg-brand/10 niuu:text-brand'
                    : 'niuu:border-border-subtle niuu:text-text-muted niuu:hover:text-text-secondary',
                )}
              >
                {base === 'last-commit' ? 'last commit' : 'default branch'}
              </button>
            ))}
          </div>
        </div>
        {filesLoading ? (
          <div className="niuu:flex niuu:h-full niuu:items-center niuu:justify-center niuu:font-mono niuu:text-sm niuu:text-text-muted">
            Loading files...
          </div>
        ) : filesError ? (
          <p
            role="alert"
            className="niuu:m-0 niuu:px-4 niuu:py-3 niuu:text-xs niuu:text-critical"
            data-testid="diff-files-error"
          >
            {filesError}
          </p>
        ) : files.length === 0 ? (
          <p
            className="niuu:m-0 niuu:px-4 niuu:py-3 niuu:text-xs niuu:text-text-muted"
            data-testid="diff-files-empty"
          >
            {diffBase === 'last-commit'
              ? 'No changes since the last commit.'
              : 'No changes against the default branch.'}
          </p>
        ) : (
          <DiffFileList
            files={files}
            selectedPath={selectedFile}
            onSelect={(path) => void selectFile(path)}
          />
        )}
      </div>
      <div className="niuu:overflow-hidden">
        <DiffViewer
          key={selected?.path}
          file={selected}
          diff={diff}
          loading={diffLoading}
          error={diffError}
          review={review}
        />
      </div>
    </div>
  );
}
