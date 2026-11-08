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

/**
 * Sessions by project. With `includeEmpty`, every active registered project gets a group
 * even when it has no sessions, as Lexi lists a project folder before its first session.
 */
export function groupByProject(
  sessions: Session[],
  projects: ForgeProject[],
  { includeEmpty = false }: { includeEmpty?: boolean } = {},
): ProjectSessionGroup[] {
  const groups = new Map<string, ProjectSessionGroup>();
  if (includeEmpty) {
    for (const project of projects) {
      if (project.status !== 'active') continue;
      groups.set(project.id, { id: project.id, label: project.name, sessions: [] });
    }
  }
  for (const session of sessions) {
    const id = session.coordination?.projectId ?? '';
    const group = groups.get(id) ?? {
      id,
      label: projects.find((project) => project.id === id)?.name ?? (id || 'Independent sessions'),
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

export interface SessionNesting {
  /** The sessions to bucket, in activity order: no other session in the list leads them. */
  roots: Session[];
  /** A root with every worker beneath it, the root first. */
  subtree: (root: Session) => Session[];
  /** Ids of sessions listed only as the path to a worker that matches, never as matches. */
  contextIds: ReadonlySet<string>;
  /** Each coordinator's worker count among every session the host knows. */
  workerCounts: ReadonlyMap<string, number>;
}

function walk(nodes: SessionTreeNode[], visit: (node: SessionTreeNode, depth: number) => void) {
  const stack = nodes.map((node) => ({ node, depth: 0 })).reverse();
  while (stack.length > 0) {
    const next = stack.pop();
    if (!next) break;
    visit(next.node, next.depth);
    for (const child of [...next.node.children].reverse())
      stack.push({ node: child, depth: next.depth + 1 });
  }
}

/**
 * Nest workers under the coordinator that leads them, whatever the list is grouped by:
 * a worker follows its coordinator into the coordinator's group. A coordinator the filters
 * leave out stays as the path to a worker that matches (`contextIds`), never silently dropped.
 *
 * `visible` are the sessions that match; `candidates` are the sessions that may be listed as
 * a path (everything but what is shown elsewhere, such as the pinned group); `known` are all
 * sessions, so a coordinator's worker count does not shrink with the filters.
 */
export function nestSessions(
  visible: Session[],
  candidates: Session[],
  known: Session[],
): SessionNesting {
  const parentOf = new Map<Session, Session>();
  for (const root of buildSessionTree(candidates)) {
    const queue = [root];
    while (queue.length > 0) {
      const node = queue.shift();
      if (!node) break;
      for (const child of node.children) {
        parentOf.set(child.session, node.session);
        queue.push(child);
      }
    }
  }

  const kept = new Set<Session>(visible);
  for (const session of visible) {
    let ancestor = parentOf.get(session);
    while (ancestor && !kept.has(ancestor)) {
      kept.add(ancestor);
      ancestor = parentOf.get(ancestor);
    }
  }
  const visibleSet = new Set(visible);
  const contextIds = new Set([...kept].filter((s) => !visibleSet.has(s)).map((s) => s.id));

  const subtrees = new Map<Session, Session[]>();
  const roots: Session[] = [];
  for (const root of buildSessionTree([...kept])) {
    const members: Session[] = [];
    walk([root], (node) => members.push(node.session));
    subtrees.set(root.session, members);
    roots.push(root.session);
  }

  const workerCounts = new Map<string, number>();
  walk(buildSessionTree(known), (node) => {
    if (node.children.length > 0) workerCounts.set(node.session.id, node.children.length);
  });

  const subtree = (root: Session): Session[] => {
    const members = subtrees.get(root);
    if (!members) throw new Error(`Session ${root.id} is not a root of this nesting`);
    return members;
  };
  return { roots, subtree, contextIds, workerCounts };
}

/**
 * Whether a session's nesting reads as coordination: a coordinator and its workers, or a
 * session with no project role. Any other role (a workflow run's member, nested under the
 * run's first session) is named by its role instead.
 */
export function coordinatesNesting(session: Session): boolean {
  return isCoordinationRole(session.coordination?.role);
}

/** The project roles whose nesting is coordination; no role reads the same way. */
export function isCoordinationRole(role: string | undefined): boolean {
  return !role || role === 'coordinator' || role === 'worker';
}

/** How a session that leads others is tagged: Coordinator, or its own role. */
export function leaderTag(role: string | undefined): string {
  return isCoordinationRole(role) ? 'Coordinator' : roleLabel(role!);
}

/** A project role as a label: `reviewer` reads as Reviewer. */
export function roleLabel(role: string): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

/** What a session that leads others says about them on its row. */
export function leadLabel(session: Session, count: number): string {
  if (coordinatesNesting(session)) {
    if (count === 0) return 'Coordinator';
    return `Coordinator · ${count} ${count === 1 ? 'worker' : 'workers'}`;
  }
  const role = roleLabel(session.coordination!.role);
  if (count === 0) return role;
  return `${role} · ${count} ${count === 1 ? 'session' : 'sessions'}`;
}

/** A session that leads others: it says so, or workers name it as their coordinator. */
export function isCoordinator(
  session: Session,
  workerCounts: ReadonlyMap<string, number>,
): boolean {
  return session.coordination?.role === 'coordinator' || (workerCounts.get(session.id) ?? 0) > 0;
}

export interface SessionCoordinationView {
  /** The session that leads this one, when the list knows it. */
  coordinator: Session | null;
  /** The sessions this one leads, most recently active first. */
  workers: Session[];
}

/** Who leads a session and whom it leads, read the way the session list nests them. */
export function coordinationOf(sessions: Session[], sessionId: string): SessionCoordinationView {
  const found: SessionCoordinationView = { coordinator: null, workers: [] };
  const visit = (nodes: SessionTreeNode[], parent: Session | null) => {
    for (const node of nodes) {
      if (node.session.id === sessionId) {
        found.coordinator = parent;
        found.workers = node.children.map((child) => child.session);
      }
      visit(node.children, node.session);
    }
  };
  visit(buildSessionTree(sessions), null);
  return found;
}
