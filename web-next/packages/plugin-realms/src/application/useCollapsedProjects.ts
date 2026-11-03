import { useCallback, useState } from 'react';

const STORAGE_KEY = 'niuu.compactUx.collapsedProjects';
/** The group of workspaces that belong to no project has no id of its own. */
const NO_PROJECT = 'none';

function read(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '[]') as unknown;
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

function write(value: string[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // localStorage unavailable; a collapsed group is a convenience only
  }
}

/** Which project groups this viewer folded away on the workspaces page. */
export function useCollapsedProjects() {
  const [collapsed, setCollapsed] = useState(read);

  const toggle = useCallback(
    (projectId: string | null) => {
      const key = projectId ?? NO_PROJECT;
      const next = collapsed.includes(key)
        ? collapsed.filter((entry) => entry !== key)
        : [...collapsed, key];
      setCollapsed(next);
      write(next);
    },
    [collapsed],
  );

  const isCollapsed = (projectId: string | null) => collapsed.includes(projectId ?? NO_PROJECT);

  return { isCollapsed, toggle };
}
