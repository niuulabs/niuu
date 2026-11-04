import type { Ravn } from '@niuulabs/plugin-ravn';
import type { ReviewItem as WorkReviewItem, Saga } from '@niuulabs/plugin-ting';
import type {
  RealmSummary,
  RealmTrustGrant,
  ReviewItem,
  ValkyrieDashboard,
  ValkyrieResident,
} from '@niuulabs/plugin-valkyrie';
import { realmSlugForEnvironment } from '@niuulabs/plugin-valkyrie';
import type { VolundrSession } from '@niuulabs/plugin-volundr';
import {
  personaNameFor,
  residentNameFor,
  slugify,
  type RealmBinding,
  type RealmView,
} from './realm';
import { sessionsWithDescendants } from './sessionTree';

/** What Work stamps on the sessions it starts for a tracker link. */
const WORK_PROJECT_LABEL = 'work-project:';

/** The realm's own resident in the Valkyrie dashboard, by environment id. */
export function residentForRealm(
  dashboard: ValkyrieDashboard | undefined,
  slug: string,
): ValkyrieResident | null {
  if (!dashboard) return null;
  // By name first: one environment can hold two residents, and each has its own workspace.
  return (
    dashboard.valkyries.find((resident) => slugify(resident.name) === residentNameFor(slug)) ??
    dashboard.valkyries.find(
      (resident) => realmSlugForEnvironment(resident.environmentId) === slug,
    ) ??
    null
  );
}

/**
 * The realm's own resident in the Ravn fleet.
 *
 * Prefers the explicit `realmId` link stored on the resident record at
 * deploy time (migration 000079). Falls back to the naming convention for
 * residents deployed before that link existed, or discovered residents that
 * carry no realm id at all.
 */
export function ravnForRealm(
  ravens: Ravn[] | undefined,
  slug: string,
  realmId?: string | null,
): Ravn | null {
  if (!ravens) return null;
  if (realmId) {
    const linked = ravens.find((ravn) => ravn.realmId === realmId);
    if (linked) return linked;
  }
  // The naming-convention fallback is only for residents that carry no
  // realmId at all (deployed before the link existed, or discovered rather
  // than managed). A ravn that IS linked to a different realm must never
  // match here just because it happens to share this realm's name/persona.
  const unlinked = ravens.filter((ravn) => !ravn.realmId);
  return (
    unlinked.find((ravn) => slugify(ravn.residentName ?? '') === residentNameFor(slug)) ??
    unlinked.find((ravn) => ravn.personaName === personaNameFor(slug)) ??
    null
  );
}

/** Sessions the realm's resident started (by its persona). */
export function sessionsForRealm(sessions: VolundrSession[] | undefined, slug: string) {
  if (!sessions) return [];
  const persona = personaNameFor(slug);
  return sessions.filter((session) => session.personaName === persona);
}

/** The Work link of a tracker board, which is how a workspace reaches its tickets. */
export function workProjectForBoard(sagas: Saga[] | undefined, board: string | undefined) {
  if (!sagas || !board) return null;
  return sagas.find((saga) => saga.trackerId === board) ?? null;
}

/** A workspace's sessions: its resident's, Work's for its board, and all they started. */
export function workspaceSessions(
  sessions: VolundrSession[] | undefined,
  slug: string,
  workProjectId: string | undefined,
) {
  const label = `${WORK_PROJECT_LABEL}${workProjectId}`;
  const seeds = [
    ...sessionsForRealm(sessions, slug),
    ...(sessions ?? []).filter((session) => session.coordination?.labels?.includes(label)),
  ];
  return sessionsWithDescendants(
    sessions,
    seeds.map((session) => session.id),
  );
}

/** What a person has to decide on a workspace's board. */
export function waitingOnBoard(
  reviews: WorkReviewItem[] | undefined,
  workProjectId: string | undefined,
): WorkReviewItem[] {
  if (!reviews || !workProjectId) return [];
  return reviews.filter((item) => item.state === 'waiting' && item.projectId === workProjectId);
}

const ACTIVE_SESSION_STATES = new Set(['running', 'provisioning', 'ready', 'awaiting_input']);

export function activeSessionCount(sessions: VolundrSession[]): number {
  return sessions.filter((session) => ACTIVE_SESSION_STATES.has(session.status)).length;
}

/** Reviews addressed to the realm's environment (the resident asking you). */
export function reviewsForRealm(
  reviews: ReviewItem[] | undefined,
  resident: ValkyrieResident | null,
): ReviewItem[] {
  if (!reviews || !resident) return [];
  return reviews.filter((item) => item.environmentId === resident.environmentId);
}

/** Where the realm looks: read back from the observe grant's limits. */
export function bindingFromGrants(grants: RealmTrustGrant[] | undefined): RealmBinding | null {
  const observe = grants?.filter((grant) => grant.action_class === 'observe').at(-1);
  if (!observe) return null;
  const limits = observe.limits as Record<string, unknown>;
  const text = (key: string) => (typeof limits[key] === 'string' ? (limits[key] as string) : '');
  return {
    template: text('template'),
    repo: observe.target === '*' ? text('repo') : observe.target,
    branch: text('branch'),
    trackerBoard: text('tracker_board'),
    bugBoard: text('bug_board') || undefined,
    mountTarget: text('mount_target'),
  };
}

/** Latest grant per action class (a higher level granted later wins). */
export function latestGrants(grants: RealmTrustGrant[] | undefined) {
  const byClass = new Map<string, RealmTrustGrant>();
  for (const grant of grants ?? []) byClass.set(grant.action_class, grant);
  return [...byClass.values()].map((grant) => ({
    actionClass: grant.action_class,
    level: grant.level,
    target: grant.target,
  }));
}

export function buildRealmView(input: {
  realm: RealmSummary;
  dashboard: ValkyrieDashboard | undefined;
  ravens: Ravn[] | undefined;
  grants: RealmTrustGrant[] | undefined;
  reviews: ReviewItem[] | undefined;
  sessions: VolundrSession[] | undefined;
}): RealmView {
  const { realm } = input;
  const resident = residentForRealm(input.dashboard, realm.slug);
  const ravn = ravnForRealm(input.ravens, realm.slug, realm.id);
  const sessions = sessionsForRealm(input.sessions, realm.slug);
  return {
    slug: realm.slug,
    name: realm.name,
    autonomyProfile: realm.autonomy_profile,
    instanceId: realm.instance_id,
    binding: bindingFromGrants(input.grants),
    personaName: personaNameFor(realm.slug),
    residentName: residentNameFor(realm.slug),
    environmentId: resident?.environmentId ?? null,
    wakefulness: resident?.wakefulness ?? null,
    autonomyMode: resident?.autonomyMode ?? null,
    confidence: resident?.confidence ?? null,
    residentStatus: resident?.status ?? ravn?.status ?? null,
    ravnId: ravn?.id ?? null,
    grants: latestGrants(input.grants),
    pendingReviews: reviewsForRealm(input.reviews, resident).filter(
      (item) => item.status === 'pending',
    ).length,
    runningSessions: activeSessionCount(sessions),
  };
}
