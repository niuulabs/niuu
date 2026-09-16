export interface WorkspaceResource {
  kind: 'workspace';
  path: string;
  name: string;
}

/** Resolve host file links into the owning workspace; never fetch another host's path. */
export function resolveSessionResource(href: string, workspace?: string): WorkspaceResource | null {
  if (!href || href.startsWith('#') || href.startsWith('//')) return null;
  let path = href.trim().replace(/^<|>$/g, '');
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
  const root = workspace?.replace(/\/+$/, '');
  if (root && path.startsWith(`${root}/`)) path = path.slice(root.length + 1);
  else if (path.startsWith('/workspace/')) path = path.slice('/workspace/'.length);
  else if (path.startsWith('/')) return null;
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
  return { kind: 'workspace', path: `/workspace/${parts.join('/')}`, name };
}
