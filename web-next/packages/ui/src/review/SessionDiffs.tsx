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
    files,
    filesLoading,
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
              <div className="niuu:bg-bg-tertiary niuu:px-4 niuu:py-0.5 niuu:text-text-muted">
                @@ -{hunk.oldStart},{hunk.oldCount} +{hunk.newStart},{hunk.newCount} @@
              </div>
              {hunk.lines.map((line, j) => {
                const side = line.type === 'remove' ? 'old' : 'new';
                const lineNumber = side === 'old' ? line.oldLine : line.newLine;
                const composerKey = `${side}:${lineNumber}`;
                return (
                  <Fragment key={j}>
                    <div
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
}: {
  chatEndpoint: string | null;
  review?: ReviewControls;
}) {
  const {
    files,
    filesLoading,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    selectFile,
    setDiffBase,
  } = useLiveDiffViewer(chatEndpoint);
  const selected = files.find((file) => file.path === selectedFile) ?? null;

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
