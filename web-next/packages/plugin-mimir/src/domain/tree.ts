/**
 * Mímir file-tree domain — builds a virtual directory tree from page paths.
 *
 * Pages live at paths like `/arch/overview` or `/api/rest/auth`. The tree
 * groups them into directory nodes so the PagesView sidebar can render a
 * collapsible file-tree navigator.
 *
 * Multi-mount union merge: pages from multiple mounts are deduplicated by
 * path before the tree is built. When the same path exists on multiple
 * mounts the last occurrence (highest-priority mount) wins.
 */

import type { PageMeta } from './page';

// ---------------------------------------------------------------------------
// Tree node types
// ---------------------------------------------------------------------------

export interface FileTreeDir {
  isDir: true;
  name: string;
  /** Slash-prefixed path to this directory node, e.g. `/arch`. */
  path: string;
  children: Record<string, FileTreeItem>;
}

export interface FileTreeLeaf {
  isDir: false;
  name: string;
  path: string;
  page: PageMeta;
}

export type FileTreeItem = FileTreeDir | FileTreeLeaf;

// ---------------------------------------------------------------------------
// Build
// ---------------------------------------------------------------------------

/** Build a virtual directory tree from a flat list of pages. */
export function buildFileTree(pages: PageMeta[]): FileTreeDir {
  const root: FileTreeDir = { isDir: true, name: '', path: '/', children: {} };
  for (const page of pages) {
    const parts = page.path.replace(/^\//, '').split('/').filter(Boolean);
    insertPage(root, parts, page, '/');
  }
  return root;
}

function insertPage(node: FileTreeDir, parts: string[], page: PageMeta, currentPath: string): void {
  const [head, ...rest] = parts;
  if (head === undefined) return;

  if (rest.length === 0) {
    node.children[head] = { isDir: false, name: head, path: page.path, page };
    return;
  }

  const dirPath = currentPath === '/' ? `/${head}` : `${currentPath}/${head}`;
  const existing = node.children[head];
  if (existing !== undefined && existing.isDir) {
    insertPage(existing, rest, page, dirPath);
    return;
  }

  const dir: FileTreeDir = { isDir: true, name: head, path: dirPath, children: {} };
  node.children[head] = dir;
  insertPage(dir, rest, page, dirPath);
}

// ---------------------------------------------------------------------------
// Multi-mount union merge
// ---------------------------------------------------------------------------

/**
 * Merge pages from multiple mounts into a single virtual tree.
 *
 * Pages are deduplicated by path. When the same path appears in multiple
 * mounts the last occurrence wins (callers should pass mounts in priority
 * order, highest priority last so it overwrites lower-priority copies).
 */
export function mergeFileTrees(pages: PageMeta[]): FileTreeDir {
  const deduped = new Map<string, PageMeta>();
  for (const page of pages) {
    deduped.set(page.path, page);
  }
  return buildFileTree([...deduped.values()]);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Count the total number of leaf (page) nodes in a subtree. */
export function countLeaves(node: FileTreeDir): number {
  let count = 0;
  for (const child of Object.values(node.children)) {
    if (child.isDir) {
      count += countLeaves(child);
    } else {
      count += 1;
    }
  }
  return count;
}

/** Collect all leaf nodes from a subtree in DFS order. */
export function collectLeaves(node: FileTreeDir): FileTreeLeaf[] {
  const leaves: FileTreeLeaf[] = [];
  for (const child of Object.values(node.children)) {
    if (child.isDir) {
      leaves.push(...collectLeaves(child));
    } else {
      leaves.push(child);
    }
  }
  return leaves;
}
