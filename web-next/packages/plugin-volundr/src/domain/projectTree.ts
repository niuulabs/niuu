import type { Session } from './session';
import type { ForgeProject } from '../models/volundr.model';

export interface SessionTreeNode {
  session: Session;
  children: SessionTreeNode[];
}
export interface ProjectSessionGroup {
  id: string;
  label: string;
  sessions: Session[];
}

export function groupByProject(
  sessions: Session[],
  projects: ForgeProject[],
): ProjectSessionGroup[] {
  const groups = new Map<string, ProjectSessionGroup>();
  for (const session of sessions) {
    const id = session.coordination?.projectId ?? '';
    const group = groups.get(id) ?? {
      id,
      label: projects.find((project) => project.id === id)?.name ?? (id || 'No project'),
      sessions: [],
    };
    group.sessions.push(session);
    groups.set(id, group);
  }
  return [...groups.values()].sort((a, b) =>
    !a.id ? 1 : !b.id ? -1 : a.label.localeCompare(b.label, undefined, { numeric: true }),
  );
}

/** Missing/filtered parents and malformed cycles must never hide sessions. */
export function buildSessionTree(sessions: Session[]): SessionTreeNode[] {
  const nodes = sessions.map((session) => ({ session, children: [] as SessionTreeNode[] }));
  const parents = new Map<SessionTreeNode, SessionTreeNode>();
  for (const node of nodes) {
    const ref = node.session.coordination?.parent;
    if (!ref) continue;
    const candidates = nodes.filter(
      (candidate) => candidate !== node && candidate.session.id === ref.sessionId,
    );
    const parent =
      candidates.length === 1
        ? candidates[0]
        : candidates.find((candidate) => candidate.session.clusterId === ref.instanceId);
    if (!parent) continue;
    let ancestor: SessionTreeNode | undefined = parent;
    while (ancestor && ancestor !== node) ancestor = parents.get(ancestor);
    if (ancestor === node) continue;
    parents.set(node, parent);
    parent.children.push(node);
  }
  const sort = (items: SessionTreeNode[]): SessionTreeNode[] =>
    items
      .sort(
        (a, b) =>
          Date.parse(b.session.lastActivityAt ?? b.session.startedAt) -
          Date.parse(a.session.lastActivityAt ?? a.session.startedAt),
      )
      .map((node) => ({ ...node, children: sort(node.children) }));
  return sort(nodes.filter((node) => !parents.has(node)));
}
