import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Folder, FileText, Search } from 'lucide-react';
import { cn } from '../utils/cn';
import type { FileTreeNode, IFileSystemPort } from './IFileSystemPort';
import { SessionFilePreview } from './SessionFilePreview';
import { QuickOpenFiles } from './QuickOpenFiles';
import './FilesPane.css';

export interface FilesPaneProps {
  sessionId: string;
  filesystem: IFileSystemPort;
  /** Paths the agent read or wrote in this session: marked in the tree, first in quick open. */
  touched?: readonly string[];
  /** Quick open, held by the page so a keyboard shortcut can open it with the pane. */
  quickOpen?: boolean;
  onQuickOpenChange?: (open: boolean) => void;
  /** Open the full file manager (upload, new folder, delete) in the main area. */
  onManage?: () => void;
}

function byKindThenName(a: FileTreeNode, b: FileTreeNode): number {
  if (a.kind !== b.kind) return a.kind === 'directory' ? -1 : 1;
  return a.name.localeCompare(b.name);
}

/** Every path the tree has loaded, for quick open. */
function loadedPaths(nodes: readonly FileTreeNode[], children: Map<string, FileTreeNode[]>) {
  const paths: string[] = [];
  const walk = (list: readonly FileTreeNode[]) => {
    for (const node of list) {
      if (node.kind === 'file') paths.push(node.path);
      const below = children.get(node.path) ?? node.children;
      if (below) walk(below);
    }
  };
  walk(nodes);
  return paths;
}

/**
 * A session's files beside the chat, as Lexi's File pane: a folder tree on the left and the
 * chosen file's preview (Markdown, code, images, PDF) on the right, under its path.
 */
export function FilesPane({
  sessionId,
  filesystem,
  touched = [],
  quickOpen: quickOpenProp,
  onQuickOpenChange,
  onManage,
}: FilesPaneProps) {
  const [roots, setRoots] = useState<FileTreeNode[] | null>(null);
  const [rootError, setRootError] = useState<string | null>(null);
  const [children, setChildren] = useState<Map<string, FileTreeNode[]>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [folderErrors, setFolderErrors] = useState<Map<string, string>>(new Map());
  const [selected, setSelected] = useState<string | null>(null);
  const [ownQuickOpen, setOwnQuickOpen] = useState(false);
  const quickOpen = quickOpenProp ?? ownQuickOpen;
  const setQuickOpen = (open: boolean) => {
    setOwnQuickOpen(open);
    onQuickOpenChange?.(open);
  };

  useEffect(() => {
    let live = true;
    filesystem
      .listTree(sessionId)
      .then((nodes) => {
        if (live) setRoots([...nodes].sort(byKindThenName));
      })
      .catch((failure: unknown) => {
        if (live)
          setRootError(
            `The session's files could not be listed: ${failure instanceof Error ? failure.message : String(failure)}`,
          );
      });
    return () => {
      live = false;
    };
  }, [filesystem, sessionId]);

  const toggleFolder = useCallback(
    (node: FileTreeNode) => {
      setExpanded((current) => {
        const next = new Set(current);
        if (next.has(node.path)) next.delete(node.path);
        else next.add(node.path);
        return next;
      });
      if (children.has(node.path) || node.children?.length) return;
      filesystem
        .expandDirectory(sessionId, node.path)
        .then((nodes) =>
          setChildren((current) =>
            new Map(current).set(node.path, [...nodes].sort(byKindThenName)),
          ),
        )
        .catch((failure: unknown) =>
          setFolderErrors((current) =>
            new Map(current).set(
              node.path,
              `Could not open ${node.name}: ${failure instanceof Error ? failure.message : String(failure)}`,
            ),
          ),
        );
    },
    [children, filesystem, sessionId],
  );

  const touchedSet = new Set(touched);
  const renderNodes = (nodes: readonly FileTreeNode[], depth: number) =>
    nodes.map((node) => {
      const open = expanded.has(node.path);
      const below = children.get(node.path) ?? node.children;
      return (
        <li key={node.path}>
          <button
            type="button"
            className={cn('niuu-files-pane__node', selected === node.path && 'is-selected')}
            style={{ paddingLeft: `${8 + depth * 14}px` }}
            aria-expanded={node.kind === 'directory' ? open : undefined}
            aria-current={selected === node.path ? 'true' : undefined}
            data-testid={`files-pane-node-${node.path}`}
            onClick={() =>
              node.kind === 'directory' ? toggleFolder(node) : setSelected(node.path)
            }
          >
            {node.kind === 'directory' ? (
              open ? (
                <ChevronDown aria-hidden="true" />
              ) : (
                <ChevronRight aria-hidden="true" />
              )
            ) : (
              <FileText aria-hidden="true" />
            )}
            <span className="niuu-files-pane__name">{node.name}</span>
            {touchedSet.has(node.path) ? (
              <span className="niuu-files-pane__touched" title="The agent touched this file">
                ●
              </span>
            ) : null}
          </button>
          {node.kind === 'directory' && open ? (
            folderErrors.get(node.path) ? (
              <p role="alert" className="niuu-files-pane__error">
                {folderErrors.get(node.path)}
              </p>
            ) : below ? (
              <ul>{renderNodes([...below].sort(byKindThenName), depth + 1)}</ul>
            ) : (
              <p className="niuu-files-pane__loading">Loading…</p>
            )
          ) : null}
        </li>
      );
    });

  const crumbs = selected ? selected.split('/').filter(Boolean) : [];
  const candidates = [
    ...touched,
    ...(roots ? loadedPaths(roots, children).filter((path) => !touchedSet.has(path)) : []),
  ];

  return (
    <div className="niuu-files-pane" data-testid="files-pane">
      <div className="niuu-files-pane__tree">
        <div className="niuu-files-pane__tree-head">
          <button
            type="button"
            className="niuu-files-pane__quick"
            onClick={() => setQuickOpen(true)}
            aria-label="Quick open a file"
          >
            <Search aria-hidden="true" />
            Quick open
            <kbd>⌥⌘O</kbd>
          </button>
        </div>
        {rootError ? (
          <p role="alert" className="niuu-files-pane__error" data-testid="files-pane-error">
            {rootError}
          </p>
        ) : roots === null ? (
          <p className="niuu-files-pane__loading">Loading files…</p>
        ) : roots.length === 0 ? (
          <p className="niuu-files-pane__loading">This workspace is empty.</p>
        ) : (
          <ul className="niuu-files-pane__list">{renderNodes(roots, 0)}</ul>
        )}
        {onManage ? (
          <button type="button" className="niuu-files-pane__manage" onClick={onManage}>
            <Folder aria-hidden="true" />
            Upload, new folder, delete…
          </button>
        ) : null}
      </div>
      <div className="niuu-files-pane__view">
        {selected ? (
          <>
            <nav className="niuu-files-pane__crumbs" aria-label="File path">
              {crumbs.map((part, index) => (
                <span key={`${index}-${part}`} aria-current={index === crumbs.length - 1}>
                  {part}
                </span>
              ))}
            </nav>
            <div className="niuu-files-pane__preview">
              <SessionFilePreview sessionId={sessionId} path={selected} filesystem={filesystem} />
            </div>
          </>
        ) : (
          <p className="niuu-files-pane__hint">Choose a file to preview it here.</p>
        )}
      </div>
      <QuickOpenFiles
        open={quickOpen}
        onOpenChange={setQuickOpen}
        candidates={candidates}
        touched={touched}
        onPick={(path) => {
          setSelected(path);
          setQuickOpen(false);
        }}
      />
    </div>
  );
}
