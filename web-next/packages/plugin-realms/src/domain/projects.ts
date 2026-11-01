import type { RealmSummary, ValkyrieDashboard, ValkyrieResident } from '@niuulabs/plugin-valkyrie';
import type { ForgeProject } from '@niuulabs/plugin-volundr';
import { residentForRealm } from './join';
import { isValidSlug, slugify } from './realm';

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
export function groupByProject<T extends { realm: RealmSummary; pendingReviews: unknown[] }>(
  cards: T[],
  projects: ForgeProject[],
): ProjectGroup<T>[] {
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
  const pending = (group: ProjectGroup<T>) =>
    group.cards.reduce((sum, card) => sum + card.pendingReviews.length, 0);
  return [...groups.values()].sort((a, b) => {
    if (a.id === null || b.id === null) return a.id === null ? 1 : -1;
    return pending(b) - pending(a) || a.name.localeCompare(b.name);
  });
}

/** Residents already running that no workspace keeps yet, such as the cluster ones. */
export function unclaimedResidents(
  dashboard: ValkyrieDashboard | undefined,
  realms: RealmSummary[] | undefined,
): ValkyrieResident[] {
  if (!dashboard) return [];
  const claimed = new Set(
    (realms ?? []).map((realm) => residentForRealm(dashboard, realm.slug)?.id ?? null),
  );
  return dashboard.valkyries.filter((resident) => !claimed.has(resident.id));
}

/** A workspace finds its resident by name, so the slug is the resident's own. */
export function slugForResident(resident: ValkyrieResident): string {
  const slug = slugify(resident.name);
  if (!isValidSlug(slug)) {
    throw new Error(`${resident.name} has no name a workspace can be found by.`);
  }
  return slug;
}
