import { useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { cn } from '@niuulabs/ui';
import type { IFileSystemPort, FileTreeNode } from '../ports/IFileSystemPort';
import { FileViewer } from './FileTree/FileViewer';
import './SessionFilesWorkspace.css';

interface SessionFilesWorkspaceProps {
  sessionId: string;
  filesystem: IFileSystemPort;
}

interface BrowserNode extends FileTreeNode {
  children?: BrowserNode[];
}

interface UploadItem {
  file: File;
  status: 'pending' | 'uploading' | 'done' | 'error';
  error?: string;
}

export function SessionFilesWorkspace({ sessionId, filesystem }: SessionFilesWorkspaceProps) {
  const queryClient = useQueryClient();
  const uploadRef = useRef<HTMLInputElement | null>(null);

  const [currentDir, setCurrentDir] = useState('/workspace');
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [dropActive, setDropActive] = useState(false);

  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [errorMessage, setErrorMessage] = useState<string>();

  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [showDownloadDialog, setShowDownloadDialog] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [showMkdirDialog, setShowMkdirDialog] = useState(false);
  const [mkdirName, setMkdirName] = useState('');

  const [viewerPath, setViewerPath] = useState<string>();
  const [viewerContent, setViewerContent] = useState('');
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerError, setViewerError] = useState<string>();

  const treeQuery = useQuery<FileTreeNode[]>({
    queryKey: ['volundr', 'filetree', sessionId],
    queryFn: () => filesystem.listTree(sessionId),
  });

  const roots = useMemo(() => normalizeRoots(treeQuery.data ?? []), [treeQuery.data]);
  const index = useMemo(() => buildIndex(roots), [roots]);

  const rootOptions = roots.map((node) => ({ name: node.name, path: node.path }));
  const currentRoot = rootOptions.find(
    (root) => currentDir === root.path || currentDir.startsWith(`${root.path}/`),
  ) ??
    rootOptions[0] ?? { name: 'workspace', path: '/workspace' };
  const currentNode = index.get(currentDir) ?? index.get(currentRoot.path);
  const breadcrumbs = buildBreadcrumbs(currentDir, index);
  const entries = sortEntries(currentNode?.children ?? []);
  const selectedNodes = selectedPaths
    .map((path) => index.get(path))
    .filter((node): node is BrowserNode => Boolean(node));
  const canDownload = selectedNodes.some((node) => node.kind === 'file' && !node.isSecret);
  const canDelete = selectedNodes.length > 0;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['volundr', 'filetree', sessionId] });
  }

  function resetViewer() {
    setViewerPath(undefined);
    setViewerContent('');
    setViewerError(undefined);
    setViewerLoading(false);
  }

  function clearSelection() {
    setSelectedPaths([]);
  }

  function selectSingle(path: string) {
    setSelectedPaths([path]);
  }

  function toggleSelection(path: string) {
    setSelectedPaths((prev) =>
      prev.includes(path) ? prev.filter((item) => item !== path) : [...prev, path],
    );
  }

  async function openFile(path: string) {
    setViewerPath(path);
    setViewerContent('');
    setViewerError(undefined);
    setViewerLoading(true);
    try {
      const content = await filesystem.readFile(sessionId, path);
      setViewerContent(content);
    } catch (err) {
      setViewerError(err instanceof Error ? err.message : 'Failed to open file');
    } finally {
      setViewerLoading(false);
    }
  }

  function handleRowClick(node: BrowserNode, multi = false) {
    if (multi) {
      toggleSelection(node.path);
      return;
    }
    selectSingle(node.path);
  }

  async function handleRowDoubleClick(node: BrowserNode) {
    if (node.kind === 'directory') {
      setCurrentDir(node.path);
      clearSelection();
      resetViewer();
      return;
    }
    if (node.isSecret) return;
    selectSingle(node.path);
    await openFile(node.path);
  }

  async function uploadFiles(files: FileList | File[]) {
    const incoming = Array.from(files);
    if (incoming.length === 0) return;

    const targetDir = currentNode?.kind === 'directory' ? currentNode.path : currentRoot.path;
    const nextUploads: UploadItem[] = incoming.map((file) => ({ file, status: 'pending' }));
    setUploads((prev) => [...prev, ...nextUploads]);

    for (const item of nextUploads) {
      item.status = 'uploading';
      setUploads((prev) => [...prev]);
      try {
        const content = await item.file.text();
        await filesystem.writeFile(sessionId, joinPath(targetDir, item.file.name), content);
        item.status = 'done';
      } catch (err) {
        item.status = 'error';
        item.error = err instanceof Error ? err.message : 'Upload failed';
      }
      setUploads((prev) => [...prev]);
    }

    setShowUploadDialog(false);
    await refresh();
  }

  async function handleDownloadSelected() {
    try {
      for (const node of selectedNodes) {
        if (node.kind !== 'file' || node.isSecret) continue;
        const content = await filesystem.readFile(sessionId, node.path);
        const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = node.name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
      }
      setShowDownloadDialog(false);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Download failed');
    }
  }

  async function handleDeleteSelected() {
    try {
      await filesystem.deletePaths(sessionId, selectedPaths);
      clearSelection();
      setShowDeleteDialog(false);
      resetViewer();
      await refresh();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Delete failed');
    }
  }

  async function handleCreateDirectory() {
    const name = mkdirName.trim();
    if (!name) return;
    try {
      await filesystem.writeFile(sessionId, joinPath(currentDir, `${name}/.keep`), '');
      setMkdirName('');
      setShowMkdirDialog(false);
      await refresh();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Create folder failed');
    }
  }

  return (
    <div
      className="niuu-relative niuu-flex niuu-h-full niuu-min-h-0 niuu-flex-col niuu-overflow-hidden niuu-bg-bg-primary"
      data-testid="session-files-workspace"
      onDragOver={(e) => {
        e.preventDefault();
        setDropActive(true);
      }}
      onDragLeave={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setDropActive(false);
      }}
      onDrop={async (e) => {
        e.preventDefault();
        setDropActive(false);
        await uploadFiles(e.dataTransfer.files);
      }}
    >
      <input
        ref={uploadRef}
        type="file"
        multiple
        hidden
        onChange={async (e) => {
          const input = e.currentTarget;
          if (input.files) await uploadFiles(input.files);
          input.value = '';
        }}
      />

      <div className="niuu-flex niuu-flex-nowrap niuu-items-center niuu-gap-3 niuu-overflow-x-auto niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-3 niuu-py-2">
        <div className="niuu-flex niuu-flex-shrink-0 niuu-overflow-hidden niuu-rounded niuu-border niuu-border-border-subtle">
          {rootOptions.map((root) => {
            const active = currentRoot.path === root.path;
            return (
              <button
                key={root.path}
                type="button"
                className={
                  active
                    ? 'niuu-border-r niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-primary last:niuu-border-r-0'
                    : 'niuu-border-r niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-muted hover:niuu-bg-bg-tertiary last:niuu-border-r-0'
                }
                onClick={() => {
                  setCurrentDir(root.path);
                  clearSelection();
                  resetViewer();
                }}
              >
                {root.name}
              </button>
            );
          })}
        </div>

        <div className="niuu-ml-auto niuu-flex niuu-flex-shrink-0 niuu-items-center niuu-gap-2">
          <ActionButton label="upload" onClick={() => setShowUploadDialog(true)} />
          <ActionButton
            label="download"
            onClick={() => setShowDownloadDialog(true)}
            disabled={!canDownload}
          />
          <ActionButton
            label="delete"
            onClick={() => setShowDeleteDialog(true)}
            disabled={!canDelete}
          />
          <ActionButton label="create folder" onClick={() => setShowMkdirDialog(true)} />
          <ActionButton label="refresh" onClick={() => void refresh()} />
        </div>
      </div>

      <div className="niuu-flex niuu-flex-nowrap niuu-items-center niuu-gap-2 niuu-overflow-x-auto niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
        <button
          type="button"
          className="niuu-inline-flex niuu-items-center niuu-gap-1 niuu-rounded-md niuu-px-1.5 niuu-py-1 hover:niuu-bg-bg-secondary hover:niuu-text-text-primary"
          onClick={() => {
            setCurrentDir(currentRoot.path);
            clearSelection();
            resetViewer();
          }}
        >
          {currentRoot.name}
        </button>
        {breadcrumbs
          .filter((crumb) => crumb.path !== currentRoot.path)
          .map((crumb) => (
            <span key={crumb.path} className="niuu-flex niuu-items-center niuu-gap-1">
              <span>›</span>
              <button
                type="button"
                className="niuu-rounded-md niuu-px-1.5 niuu-py-1 hover:niuu-bg-bg-secondary hover:niuu-text-text-primary"
                onClick={() => {
                  setCurrentDir(crumb.path);
                  clearSelection();
                  resetViewer();
                }}
              >
                {crumb.name}
              </button>
            </span>
          ))}
      </div>

      <div className="niuu-flex niuu-flex-nowrap niuu-items-center niuu-justify-between niuu-gap-4 niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-3 niuu-py-2">
        <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-whitespace-nowrap">
          <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
            {entries.length} item{entries.length === 1 ? '' : 's'}
          </div>
        </div>
        <div className="niuu-flex-shrink-0 niuu-whitespace-nowrap niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
          {selectedPaths.length === 0 ? 'no selection' : `${selectedPaths.length} selected`}
        </div>
      </div>

      <section className="niuu-min-h-0 niuu-flex niuu-flex-1 niuu-flex-col niuu-overflow-hidden">
        <div
          className="niuu-grid niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-muted"
          style={{ gridTemplateColumns: 'minmax(0, 1.9fr) 128px 112px' }}
        >
          <div>Name</div>
          <div>Kind</div>
          <div className="niuu-text-right">Size</div>
        </div>

        <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-auto">
          {treeQuery.isLoading && (
            <div className="niuu-p-4 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              loading files…
            </div>
          )}
          {treeQuery.isError && (
            <div className="niuu-p-4 niuu-font-mono niuu-text-[11px] niuu-text-critical">
              failed to load files
            </div>
          )}
          {!treeQuery.isLoading && !treeQuery.isError && (
            <table className="niuu-session-files-table niuu-w-full niuu-table-fixed">
              <colgroup>
                <col />
                <col style={{ width: '128px' }} />
                <col style={{ width: '112px' }} />
              </colgroup>
              <tbody>
                {entries.map((node) => {
                  const selected = selectedPaths.includes(node.path);
                  return (
                    <tr
                      key={node.path}
                      className="niuu-session-files-row niuu-cursor-pointer niuu-border-b niuu-border-border-subtle niuu-transition-colors"
                      data-selected={selected ? 'true' : 'false'}
                      data-testid={`file-browser-row-${node.path}`}
                      onClick={(e) => handleRowClick(node, e.metaKey || e.ctrlKey || e.shiftKey)}
                      onDoubleClick={() => void handleRowDoubleClick(node)}
                    >
                      <td className="niuu-session-files-cell niuu-session-files-cell--name niuu-px-2 niuu-py-1">
                        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-overflow-hidden">
                          <span
                            aria-hidden="true"
                            className={cn(
                              'niuu-h-2 niuu-w-2 niuu-flex-shrink-0 niuu-rounded-full niuu-transition-colors',
                              selected ? 'niuu-bg-brand' : 'niuu-bg-transparent',
                            )}
                          />
                          <span className="niuu-inline-flex niuu-h-4 niuu-w-4 niuu-flex-shrink-0 niuu-items-center niuu-justify-center">
                            <NodeGlyph kind={node.kind} />
                          </span>
                          <span
                            className={cn(
                              'niuu-truncate niuu-font-mono niuu-text-[14px] niuu-leading-5',
                              selected ? 'niuu-text-text-primary' : 'niuu-text-text-primary',
                            )}
                          >
                            {node.name}
                          </span>
                          {node.isSecret && (
                            <span className="niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-text-text-muted">
                              secret
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="niuu-session-files-cell niuu-px-2 niuu-py-1">
                        <span className="niuu-inline-flex niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-tracking-[0.12em] niuu-text-text-muted">
                          {node.kind === 'directory' ? 'folder' : 'file'}
                        </span>
                      </td>
                      <td className="niuu-session-files-cell niuu-px-2 niuu-py-1 niuu-whitespace-nowrap niuu-text-right niuu-font-mono niuu-text-[12px] niuu-text-text-muted">
                        {node.kind === 'file' && node.size != null ? formatSize(node.size) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {!treeQuery.isLoading && !treeQuery.isError && entries.length === 0 && (
            <div className="niuu-p-6 niuu-text-center niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              empty directory
            </div>
          )}
        </div>
      </section>

      <div className="niuu-border-t niuu-border-border-subtle niuu-bg-bg-primary">
        <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-px-2 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
          <span>
            {selectedPaths.length === 0 ? 'no files selected' : `${selectedPaths.length} selected`}
          </span>
          <span>cmd/ctrl-click to multi-select</span>
          <span>drag files in to upload</span>
        </div>
        {uploads.length > 0 && (
          <>
            <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-t niuu-border-border-subtle niuu-px-3 niuu-py-2">
              <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-secondary">
                uploads ({uploads.filter((u) => u.status === 'done').length}/{uploads.length})
              </span>
              <button
                type="button"
                className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted"
                onClick={() =>
                  setUploads((prev) =>
                    prev.filter((u) => u.status !== 'done' && u.status !== 'error'),
                  )
                }
              >
                clear
              </button>
            </div>
            <div className="niuu-max-h-28 niuu-overflow-auto niuu-border-t niuu-border-border-subtle">
              {uploads.map((item, index) => (
                <div
                  key={`${item.file.name}-${index}`}
                  className="niuu-grid niuu-grid-cols-[1fr_72px_96px] niuu-gap-3 niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary"
                >
                  <span className="niuu-truncate">{item.file.name}</span>
                  <span className="niuu-text-text-muted">{formatSize(item.file.size)}</span>
                  <span
                    className={
                      item.status === 'error'
                        ? 'niuu-text-critical'
                        : item.status === 'done'
                          ? 'niuu-text-emerald-400'
                          : 'niuu-text-text-muted'
                    }
                  >
                    {item.status === 'done'
                      ? 'done'
                      : item.status === 'error'
                        ? (item.error ?? 'failed')
                        : item.status}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {dropActive && (
        <div className="niuu-pointer-events-none niuu-absolute niuu-inset-0 niuu-z-20 niuu-flex niuu-flex-col niuu-items-center niuu-justify-center niuu-gap-2 niuu-bg-bg-primary/80">
          <div className="niuu-font-mono niuu-text-[18px] niuu-text-brand">upload</div>
          <div className="niuu-font-mono niuu-text-[12px] niuu-text-brand">
            drop files to upload
          </div>
        </div>
      )}

      {errorMessage && (
        <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-border-t niuu-border-critical niuu-bg-critical/10 niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-[11px] niuu-text-critical">
          <span>{errorMessage}</span>
          <button type="button" className="niuu-ml-auto" onClick={() => setErrorMessage(undefined)}>
            dismiss
          </button>
        </div>
      )}

      {showUploadDialog && (
        <DialogShell title="Upload files" onClose={() => setShowUploadDialog(false)}>
          <div className="niuu-space-y-3">
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              target: {currentDir}
            </div>
            <button
              type="button"
              className="niuu-w-full niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-3 niuu-py-3 niuu-font-mono niuu-text-[12px] niuu-text-text-primary"
              onClick={() => uploadRef.current?.click()}
            >
              choose files…
            </button>
          </div>
        </DialogShell>
      )}

      {showDownloadDialog && (
        <DialogShell title="Download selected files" onClose={() => setShowDownloadDialog(false)}>
          <div className="niuu-space-y-3">
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              {selectedNodes.length} selected
            </div>
            <div className="niuu-max-h-40 niuu-space-y-1 niuu-overflow-auto niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-p-2">
              {selectedNodes.map((node) => (
                <div
                  key={node.path}
                  className="niuu-font-mono niuu-text-[11px] niuu-text-text-secondary"
                >
                  {node.name}
                </div>
              ))}
            </div>
            <div className="niuu-flex niuu-justify-end">
              <button
                type="button"
                className="niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-primary"
                onClick={() => void handleDownloadSelected()}
              >
                download
              </button>
            </div>
          </div>
        </DialogShell>
      )}

      {showDeleteDialog && (
        <DialogShell title="Delete selected files" onClose={() => setShowDeleteDialog(false)}>
          <div className="niuu-space-y-3">
            <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              This will remove {selectedNodes.length} item{selectedNodes.length === 1 ? '' : 's'}.
            </div>
            <div className="niuu-flex niuu-justify-end">
              <button
                type="button"
                className="niuu-rounded niuu-border niuu-border-critical niuu-bg-critical/10 niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-critical"
                onClick={() => void handleDeleteSelected()}
              >
                delete
              </button>
            </div>
          </div>
        </DialogShell>
      )}

      {showMkdirDialog && (
        <DialogShell title="Create folder" onClose={() => setShowMkdirDialog(false)}>
          <div className="niuu-space-y-3">
            <input
              className="niuu-w-full niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-primary niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-[12px] niuu-text-text-primary"
              value={mkdirName}
              onChange={(e) => setMkdirName(e.target.value)}
              placeholder="folder-name"
              autoFocus
              onKeyDown={async (e) => {
                if (e.key === 'Enter') await handleCreateDirectory();
                if (e.key === 'Escape') setShowMkdirDialog(false);
              }}
            />
            <div className="niuu-flex niuu-justify-end">
              <button
                type="button"
                className="niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-primary"
                onClick={() => void handleCreateDirectory()}
              >
                create
              </button>
            </div>
          </div>
        </DialogShell>
      )}

      {viewerPath && (
        <div className="niuu-absolute niuu-inset-0 niuu-z-30 niuu-bg-black/40">
          <div className="niuu-absolute niuu-inset-x-8 niuu-bottom-8 niuu-top-8 niuu-overflow-hidden niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-primary">
            <FileViewer
              path={viewerPath}
              content={viewerContent}
              isLoading={viewerLoading}
              error={viewerError}
              onClose={resetViewer}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ActionButton({
  label,
  onClick,
  disabled,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className="niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-2.5 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-secondary hover:niuu-bg-bg-tertiary disabled:niuu-opacity-40"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
    >
      {label}
    </button>
  );
}

function NodeGlyph({ kind }: { kind: BrowserNode['kind'] }) {
  if (kind === 'directory') {
    return (
      <svg
        aria-hidden="true"
        viewBox="0 0 12 12"
        className="niuu-h-3.5 niuu-w-3.5 niuu-fill-current niuu-text-text-secondary"
      >
        <path d="M3 2.5L9 6L3 9.5V2.5Z" />
      </svg>
    );
  }

  return (
    <span
      aria-hidden="true"
      className="niuu-inline-block niuu-h-1.5 niuu-w-1.5 niuu-rounded-full niuu-bg-text-faint"
    />
  );
}

function DialogShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <div
        className="niuu-absolute niuu-inset-0 niuu-z-30 niuu-bg-black/40"
        onClick={onClose}
        role="presentation"
      />
      <div className="niuu-absolute niuu-left-1/2 niuu-top-1/2 niuu-z-40 niuu-w-[360px] niuu--translate-x-1/2 niuu--translate-y-1/2 niuu-rounded niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-4">
        <div className="niuu-mb-3 niuu-flex niuu-items-center niuu-justify-between">
          <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-primary">{title}</div>
          <button
            type="button"
            className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted"
            onClick={onClose}
          >
            close
          </button>
        </div>
        {children}
      </div>
    </>
  );
}

export function normalizeRoots(nodes: FileTreeNode[]): BrowserNode[] {
  const workspaceChildren = nodes.filter((node) => !node.mountName);
  const mountRoots = nodes.filter((node) => node.mountName).map(cloneNode);
  const roots: BrowserNode[] = [];
  if (workspaceChildren.length > 0) {
    roots.push({
      name: 'workspace',
      path: '/workspace',
      kind: 'directory',
      children: workspaceChildren.map(cloneNode),
    });
  }
  return [...roots, ...mountRoots];
}

export function cloneNode(node: FileTreeNode): BrowserNode {
  return {
    ...node,
    children: node.children?.map(cloneNode),
  };
}

export function buildIndex(nodes: BrowserNode[]): Map<string, BrowserNode> {
  const map = new Map<string, BrowserNode>();
  const visit = (node: BrowserNode) => {
    map.set(node.path, node);
    node.children?.forEach(visit);
  };
  nodes.forEach(visit);
  return map;
}

export function buildBreadcrumbs(path: string, index: Map<string, BrowserNode>) {
  const crumbs: Array<{ path: string; name: string }> = [];
  let current = path;
  while (current) {
    const node = index.get(current);
    crumbs.unshift({ path: current, name: node?.name ?? current.split('/').at(-1) ?? current });
    if (current === '/workspace' || current === '/mnt/secrets') break;
    current = current.slice(0, current.lastIndexOf('/')) || '/workspace';
    if (current === '/') break;
  }
  return crumbs;
}

export function joinPath(base: string, name: string) {
  if (base.endsWith('/')) return `${base}${name}`;
  return `${base}/${name}`;
}

export function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function sortEntries(nodes: BrowserNode[]) {
  return [...nodes].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'directory' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}
