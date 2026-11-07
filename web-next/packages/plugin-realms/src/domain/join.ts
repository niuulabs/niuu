import type { ReviewItem as WorkReviewItem, Saga } from '@niuulabs/plugin-ting';
import type { VolundrSession } from '@niuulabs/plugin-volundr';
import type { RealmSummary, RealmTrustGrant } from './governance';
import type { RealmBinding, RealmView } from './realm';
import { sessionsWithDescendants } from './sessionTree';

/** What Work stamps on the sessions it starts for a tracker link. */
const WORK_PROJECT_LABEL = 'work-project:';

/** The Work link of a tracker board, which is how a workspace reaches its tickets. */
export function workProjectForBoard(sagas: Saga[] | undefined, board: string | undefined) {
  if (!sagas || !board) return null;
  return sagas.find((saga) => saga.trackerId === board) ?? null;
}

/** A workspace's sessions: the ones Work started for its board, and all they started. */
export function workspaceSessions(
  sessions: VolundrSession[] | undefined,
  workProjectId: string | undefined,
) {
  const label = `${WORK_PROJECT_LABEL}${workProjectId}`;
  const seeds = (sessions ?? []).filter((session) => session.coordination?.labels?.includes(label));
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
  grants: RealmTrustGrant[] | undefined;
}): RealmView {
  const { realm } = input;
  const binding = bindingFromGrants(input.grants);
  // The create recipe always names where the workspace memory goes; a workspace made
  // without it has no memory mount of its own.
  const adopted = !binding?.mountTarget;
  return {
    slug: realm.slug,
    name: realm.name,
    autonomyProfile: realm.autonomy_profile,
    binding,
    adopted,
    grants: latestGrants(input.grants),
  };
}
