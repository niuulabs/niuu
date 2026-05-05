import { useState, useCallback } from 'react';
import { cn } from '@niuulabs/ui';
import type { FileTreeNode } from '../../ports/IFileSystemPort';

export interface FileTreeProps {
  /** Flat or nested tree of nodes for the session workspace. */
  nodes: FileTreeNode[];
  /** Called when the user clicks a non-secret file. */
  onOpenFile?: (path: string, mountName?: string) => void;
  /** Highlight the currently open file. */
  activePath?: string;
}

export function FileTree({ nodes, onOpenFile, activePath }: FileTreeProps) {
  if (nodes.length === 0) {
    return (
      <p className="niuu-p-4 niuu-text-sm niuu-text-text-muted" data-testid="filetree-empty">
        No files found in this session workspace.
      </p>
    );
  }

  return (
    <ul
      className="niuu-m-0 niuu-list-none niuu-p-2 niuu-font-mono niuu-text-[12px]"
      role="tree"
      aria-label="session file tree"
      data-testid="filetree-root"
    >
      {nodes.map((node) => (
        <FileTreeItem
          key={node.path}
          node={node}
          depth={0}
          onOpenFile={onOpenFile}
          activePath={activePath}
        />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// FileTreeItem — recursive row renderer
// ---------------------------------------------------------------------------

interface FileTreeItemProps {
  node: FileTreeNode;
  depth: number;
  onOpenFile?: (path: string, mountName?: string) => void;
  activePath?: string;
}

function FileTreeItem({ node, depth, onOpenFile, activePath }: FileTreeItemProps) {
  // All top-level dirs start expanded for discoverability; deeper nodes collapsed.
  const [expanded, setExpanded] = useState(depth < 1);

  const isDirectory = node.kind === 'directory';
  const isActive = activePath === node.path;
  const isMountBoundary = node.mountName !== undefined && depth === 0;

  const handleClick = useCallback(() => {
    if (isDirectory) {
      setExpanded((prev) => !prev);
      return;
    }
    if (node.isSecret) return; // never open secret files
    onOpenFile?.(node.path, node.mountName);
  }, [isDirectory, node.isSecret, node.path, node.mountName, onOpenFile]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handleClick();
      }
    },
    [handleClick],
  );

  const indentPx = depth * 16;

  return (
    <li
      role="treeitem"
      aria-expanded={isDirectory ? expanded : undefined}
      aria-selected={isActive}
      data-testid={`filetree-node-${node.path}`}
    >
      {isMountBoundary && (
        <div
          className="niuu-mt-2 niuu-flex niuu-items-center niuu-gap-2 niuu-border-t niuu-border-border-subtle niuu-px-2 niuu-py-1 niuu-text-[10px] niuu-uppercase niuu-tracking-[0.16em] niuu-text-text-muted"
          data-testid={`filetree-mount-${node.mountName}`}
        >
          <span aria-hidden>⊕</span>
          <span>mount: {node.mountName}</span>
        </div>
      )}

      <div
        className={cn(
          'niuu-flex niuu-cursor-pointer niuu-items-center niuu-gap-1.5 niuu-rounded-md niuu-border niuu-border-transparent niuu-px-2 niuu-py-1.5 niuu-transition-colors',
          isActive
            ? 'niuu-border-brand/60 niuu-bg-brand/10 niuu-text-text-primary'
            : 'niuu-text-text-secondary hover:niuu-border-border-subtle hover:niuu-bg-bg-tertiary hover:niuu-text-text-primary',
          node.isSecret && 'niuu-opacity-60',
        )}
        style={{ paddingLeft: `${indentPx + 8}px` }}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        tabIndex={0}
        role="button"
        aria-label={
          node.isSecret
            ? `${node.name} (secret — content hidden)`
            : isDirectory
              ? `${expanded ? 'collapse' : 'expand'} ${node.name}`
              : `open ${node.name}`
        }
      >
        {/* Icon */}
        <span className="niuu-shrink-0 niuu-text-xs" aria-hidden>
          {isDirectory ? (expanded ? '▾' : '▸') : node.isSecret ? '🔒' : '·'}
        </span>

        {/* Name */}
        <span className="niuu-truncate niuu-font-medium">{node.name}</span>

        {/* Secret badge */}
        {node.isSecret && (
          <span
            className="niuu-ml-auto niuu-shrink-0 niuu-rounded niuu-bg-bg-elevated niuu-px-1 niuu-text-xs niuu-text-text-muted"
            data-testid={`filetree-secret-badge-${node.path}`}
          >
            secret
          </span>
        )}

        {/* File size */}
        {!isDirectory && !node.isSecret && node.size !== undefined && (
          <span className="niuu-ml-auto niuu-shrink-0 niuu-text-[11px] niuu-text-text-muted">
            {formatSize(node.size)}
          </span>
        )}
      </div>

      {isDirectory && expanded && node.children && node.children.length > 0 && (
        <ul className="niuu-m-0 niuu-list-none niuu-p-0" role="group">
          {node.children.map((child) => (
            <FileTreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onOpenFile={onOpenFile}
              activePath={activePath}
            />
          ))}
        </ul>
      )}

      {isDirectory && expanded && (!node.children || node.children.length === 0) && (
        <div
          className="niuu-py-0.5 niuu-text-xs niuu-text-text-muted"
          style={{ paddingLeft: `${(depth + 1) * 16 + 8}px` }}
          data-testid={`filetree-empty-dir-${node.path}`}
        >
          (empty)
        </div>
      )}
    </li>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1_024) return `${bytes}B`;
  if (bytes < 1_024 * 1_024) return `${(bytes / 1_024).toFixed(1)}KB`;
  return `${(bytes / (1_024 * 1_024)).toFixed(1)}MB`;
}
