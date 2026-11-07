export interface WorkspaceResource {
  kind: 'workspace';
  path: string;
  name: string;
}

/** A file the session presented: Forge keeps a durable copy, read by its id. */
export interface PresentedFileRef {
  fileId: string;
  name: string;
  mime?: string;
  /** Where it was in the session (`/workspace/...` or `/home/...`), when the broker recorded it. */
  path?: string;
}

export interface PresentedResource {
  kind: 'presented';
  path: string;
  name: string;
  mime?: string;
}

interface PartLike {
  type?: string;
  name?: string;
  input?: unknown;
}

/** The files a conversation presented, from its `present_file` tool calls. */
export function presentedFilesIn(
  messages: readonly { parts?: readonly PartLike[] | undefined }[],
): PresentedFileRef[] {
  const files: PresentedFileRef[] = [];
  for (const message of messages) {
    for (const part of message.parts ?? []) {
      if (part.type !== 'tool_use' || part.name !== 'present_file') continue;
      const input = (part.input ?? {}) as Record<string, unknown>;
      const fileId = typeof input.file_id === 'string' ? input.file_id : '';
      if (!fileId) continue;
      files.push({
        fileId,
        name: typeof input.name === 'string' ? input.name : fileId,
        ...(typeof input.mime === 'string' ? { mime: input.mime } : {}),
        ...(typeof input.path === 'string' ? { path: input.path } : {}),
      });
    }
  }
  return files;
}

const HOME_PREFIX = '~/';

/**
 * A link to a file the session presented opens Forge's durable copy, which outlives the
 * session. Matched by the path the broker recorded, or, for a bare file name, by the last
 * presented file of that name.
 */
export function presentedResource(
  href: string,
  workspace: WorkspaceResource | null,
  presented: readonly PresentedFileRef[],
): PresentedResource | null {
  const bare = !href.includes('/');
  const match = [...presented]
    .reverse()
    .find(
      (file) =>
        (workspace !== null && file.path === workspace.path) ||
        (bare && file.path !== undefined && file.path.split('/').at(-1) === href.trim()),
    );
  if (!match) return null;
  return {
    kind: 'presented',
    path: match.fileId,
    name: match.name,
    ...(match.mime ? { mime: match.mime } : {}),
  };
}

/** Resolve host file links into the owning workspace; never fetch another host's path. */
export function resolveSessionResource(href: string, workspace?: string): WorkspaceResource | null {
  if (!href || href.startsWith('#') || href.startsWith('//')) return null;
  let path = href.trim().replace(/^<|>$/g, '');
  // A bare README.md:12 is an editor reference, not a URL scheme.
  if (/\.[a-z\d]+:\d+(?::\d+)?$/i.test(path)) path = path.replace(/:\d+(?::\d+)?$/, '');
  if (/^file:\/\//i.test(path)) {
    try {
      const url = new URL(path);
      if (url.hostname && url.hostname !== 'localhost') return null;
      path = url.pathname;
    } catch {
      return null;
    }
  } else if (/^[a-z][a-z\d+.-]*:/i.test(path)) return null;
  path = path.split('#')[0]?.replace(/:\d+(?::\d+)?$/, '') ?? '';
  try {
    path = decodeURIComponent(path);
  } catch {
    return null;
  }
  if (path.includes('\0') || path.includes('\\')) return null;
  // `~/x` is in the session's home, which the session's files are browsed as `/home`.
  if (path.startsWith(HOME_PREFIX)) return contained('/home', path.slice(HOME_PREFIX.length));
  const root = workspace === undefined ? undefined : trimTrailingSlashes(workspace);
  if (root && path.startsWith(`${root}/`)) path = path.slice(root.length + 1);
  else if (path.startsWith('/workspace/')) path = path.slice('/workspace/'.length);
  else if (path.startsWith('/')) return null;
  return contained('/workspace', path);
}

/** `root` without trailing slashes, in linear time (a `/\/+$/` replace is quadratic). */
function trimTrailingSlashes(root: string): string {
  let end = root.length;
  while (end > 0 && root[end - 1] === '/') end -= 1;
  return root.slice(0, end);
}

/** A path inside one root, never climbing out of it. */
function contained(root: '/workspace' | '/home', path: string): WorkspaceResource | null {
  const parts: string[] = [];
  for (const part of path.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') {
      if (parts.length === 0) return null;
      parts.pop();
    } else parts.push(part);
  }
  const name = parts.at(-1);
  if (!name) return null;
  return { kind: 'workspace', path: `${root}/${parts.join('/')}`, name };
}
