import type { ForgeProject } from '@niuulabs/plugin-volundr';
import type { RealmSummary } from './governance';

/** The picker's value for a project that does not exist yet. */
export const NEW_PROJECT = 'new';

export interface ProjectChoice {
  /** An existing project's id, `NEW_PROJECT`, or empty for none. */
  projectId: string;
  newProjectName: string;
}

export interface ProjectGroup<T> {
  /** Null for the workspaces that belong to no project. */
  id: string | null;
  name: string;
  project: ForgeProject | null;
  cards: T[];
}

/** Workspaces under the project they serve, the ones with no project last. */
export function groupByProject<
  T extends { realm: RealmSummary; waitingTickets: readonly unknown[] },
>(cards: T[], projects: ForgeProject[]): ProjectGroup<T>[] {
  const groups = new Map<string | null, ProjectGroup<T>>();
  for (const card of cards) {
    const id = card.realm.project_id ?? null;
    const project = projects.find((entry) => entry.id === id) ?? null;
    const group = groups.get(id) ?? {
      id,
      name: id === null ? 'Not in a project' : (project?.name ?? `Project ${id.slice(0, 8)}`),
      project,
      cards: [],
    };
    group.cards.push(card);
    groups.set(id, group);
  }
  const waiting = (group: ProjectGroup<T>) =>
    group.cards.reduce((sum, card) => sum + card.waitingTickets.length, 0);
  return [...groups.values()].sort((a, b) => {
    if (a.id === null || b.id === null) return a.id === null ? 1 : -1;
    return waiting(b) - waiting(a) || a.name.localeCompare(b.name);
  });
}

/** Live projects no workspace serves yet, which no group can show. */
export function projectsWithoutWorkspaces(
  cards: { realm: RealmSummary }[],
  projects: ForgeProject[],
): ForgeProject[] {
  const served = new Set(cards.map((card) => card.realm.project_id));
  return projects
    .filter((project) => project.status === 'active' && !served.has(project.id))
    .sort((a, b) => a.name.localeCompare(b.name));
}
