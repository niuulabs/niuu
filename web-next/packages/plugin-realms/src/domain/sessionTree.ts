import type { VolundrSession } from '@niuulabs/plugin-volundr';

export interface SessionRow {
  session: VolundrSession;
  depth: number;
}

function parentOf(session: VolundrSession): string | null {
  return session.coordination?.parent?.sessionId ?? null;
}

/** The sessions named, with everything they launched below them. */
export function sessionsWithDescendants(
  sessions: VolundrSession[] | undefined,
  seedIds: string[],
): VolundrSession[] {
  if (!sessions) return [];
  const included = new Set(seedIds);
  let grew = true;
  while (grew) {
    grew = false;
    for (const session of sessions) {
      const parent = parentOf(session);
      if (parent && included.has(parent) && !included.has(session.id)) {
        included.add(session.id);
        grew = true;
      }
    }
  }
  return sessions.filter((session) => included.has(session.id));
}

/** Parents first, each followed by what it started. A missing parent makes a root. */
export function sessionRows(sessions: VolundrSession[]): SessionRow[] {
  const ids = new Set(sessions.map((session) => session.id));
  const rows: SessionRow[] = [];
  const seen = new Set<string>();
  const visit = (session: VolundrSession, depth: number) => {
    if (seen.has(session.id)) return;
    seen.add(session.id);
    rows.push({ session, depth });
    for (const child of sessions) {
      if (parentOf(child) === session.id) visit(child, depth + 1);
    }
  };
  for (const session of sessions) {
    const parent = parentOf(session);
    if (!parent || !ids.has(parent)) visit(session, 0);
  }
  // A cycle has no root, and must still be shown.
  for (const session of sessions) visit(session, 0);
  return rows;
}
