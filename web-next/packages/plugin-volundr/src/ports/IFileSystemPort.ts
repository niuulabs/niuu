/** Represents a single node in a session file tree. */
export interface FileTreeNode {
  /** Display name (basename). */
  name: string;
  /** Absolute path within the session workspace. */
  path: string;
  kind: 'file' | 'directory';
  /** File size in bytes — undefined for directories. */
  size?: number;
  /** Child nodes populated for directories when expanded. */
  children?: FileTreeNode[];
  /** Mount name this node belongs to — undefined for workspace root. */
  mountName?: string;
  /**
   * True when the node belongs to a secret mount. Content is masked —
   * the viewer must never render the actual bytes.
   */
  isSecret?: boolean;
}

/** Port for browsing the file system of a running session pod. */
export interface IFileSystemPort {
  /**
   * Return the top-level tree for a session.
   * Directories have `children` pre-populated one level deep.
   */
  listTree(sessionId: string): Promise<FileTreeNode[]>;

  /**
   * Expand a directory, returning its direct children.
   * Used for lazy-loading deeper subtrees.
   */
  expandDirectory(sessionId: string, path: string): Promise<FileTreeNode[]>;

  /**
   * Return the raw text content of a file.
   * Throws if the path belongs to a secret mount.
   */
  readFile(sessionId: string, path: string): Promise<string>;

  /** Write or overwrite a file in the session workspace. */
  writeFile(sessionId: string, path: string, content: string): Promise<void>;

  /** Delete one or more paths from the session workspace. */
  deletePaths(sessionId: string, paths: string[]): Promise<void>;
}
