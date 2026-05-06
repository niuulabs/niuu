import { useState } from 'react';
import { countLeaves } from '../../domain';
import { PageTypeGlyph } from './PageTypeGlyph';
import type { FileTreeItem } from '../../domain/tree';
import type { PageMeta } from '../../domain/page';

/** Indent step in px per depth level — matches the CSS variable default. */
const TREE_INDENT_STEP_PX = 12;

interface TreeNodeProps {
  node: FileTreeItem;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  /** O(1) set of known page paths (both `/slug` and `slug` forms). */
  knownPaths: Set<string>;
}

/** Returns true when at least one of the page's related slugs is not in knownPaths. */
function pageHasBrokenLinks(page: PageMeta, knownPaths: Set<string>): boolean {
  if (!page.related || page.related.length === 0) return false;
  return page.related.some((slug) => !knownPaths.has(`/${slug}`) && !knownPaths.has(slug));
}

export function TreeNode({ node, depth, selectedPath, onSelect, knownPaths }: TreeNodeProps) {
  const [open, setOpen] = useState(depth <= 1);

  if (!node.isDir) {
    const conf = node.page.confidence;
    const hasBrokenLinks = pageHasBrokenLinks(node.page, knownPaths);
    const isActive = selectedPath === node.path;

    return (
      <button
        type="button"
        className={[
          'niuu-flex niuu-items-center niuu-gap-1.5 niuu-py-[2px] niuu-pr-2',
          'niuu-text-[11px] niuu-font-mono niuu-text-text-secondary',
          'niuu-cursor-pointer niuu-border-none niuu-bg-transparent niuu-w-full niuu-text-left',
          'hover:niuu-bg-bg-tertiary hover:niuu-text-text-primary',
          isActive
            ? 'niuu-bg-[color-mix(in_srgb,var(--brand-300)_14%,transparent)] niuu-text-text-primary'
            : '',
        ]
          .filter(Boolean)
          .join(' ')}
        style={{ paddingLeft: `calc(${depth * TREE_INDENT_STEP_PX}px + var(--space-3))` }}
        onClick={() => onSelect(node.path)}
        aria-current={isActive ? 'page' : undefined}
      >
        <span className={`mm-conf-dot mm-conf-dot--${conf}`} aria-label={`confidence: ${conf}`} />
        <span className="niuu-flex-1 niuu-overflow-hidden niuu-text-ellipsis niuu-whitespace-nowrap">
          {node.name}
        </span>
        {hasBrokenLinks && (
          <span
            className="niuu-inline-block niuu-w-1.5 niuu-h-1.5 niuu-rounded-full niuu-bg-brand-400 niuu-flex-shrink-0"
            aria-label="page has broken wikilinks"
            title="This page has broken wikilinks"
          />
        )}
        <PageTypeGlyph type={node.page.type} size={TREE_INDENT_STEP_PX - 1} />
      </button>
    );
  }

  const childCount = countLeaves(node);

  return (
    <div className="niuu-select-none">
      <div
        className="niuu-flex niuu-items-center niuu-gap-1.5 niuu-py-[2px] niuu-pr-2 niuu-text-[11px] niuu-font-mono niuu-text-text-muted niuu-font-medium niuu-cursor-pointer hover:niuu-text-text-secondary"
        style={{ paddingLeft: `calc(${depth * TREE_INDENT_STEP_PX}px + 4px)` }}
        onClick={() => setOpen((o) => !o)}
        role="button"
        aria-expanded={open}
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setOpen((o) => !o)}
      >
        <span className="niuu-text-[8px] niuu-w-[10px] niuu-text-center">{open ? '▾' : '▸'}</span>
        <span>{node.name}/</span>
        <span className="niuu-ml-auto niuu-font-mono niuu-text-[9px] niuu-text-text-faint">
          {childCount}
        </span>
      </div>
      {open &&
        Object.values(node.children).map((child) => (
          <TreeNode
            key={child.path}
            node={child}
            depth={depth + 1}
            selectedPath={selectedPath}
            onSelect={onSelect}
            knownPaths={knownPaths}
          />
        ))}
    </div>
  );
}
