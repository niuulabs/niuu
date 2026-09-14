/**
 * Joins for the Residents board.
 *
 * The Valkyrie side of a resident — how awake it is, what it last did, what is
 * waiting for an answer — is served by the `valkyrie*` services. Plugin-ravn
 * does not depend on plugin-valkyrie, so the shapes below name only the fields
 * this board reads. Anything richer belongs to the Valkyrie plugin itself.
 */

import type { Ravn } from './ravn';
import { nameForRavn, realmSlugForRavn } from './residentActions';

/** One resident as the Valkyrie dashboard reports it. */
export interface ValkyrieResidentView {
  id: string;
  name: string;
  environmentId: string;
  wakefulness: string;
  lastActionAt?: string;
}

/** Something a resident did, as the Valkyrie dashboard reports it. */
export interface ValkyrieActionView {
  id: string;
  title: string;
  ownerValkyrieId: string;
  environmentId: string;
  startedAt?: string;
  finishedAt?: string;
}

/** A review waiting for a human answer. */
export interface PendingReviewView {
  itemId: string;
  environmentId: string;
}

/** A realm the governance service knows about. */
export interface RealmView {
  slug: string;
  name: string;
}

/** What the card says about a resident's last move. */
export interface ResidentActivity {
  label: string;
  at: string | null;
}

/**
 * Environment ids are slugs behind a deployment prefix (`env-k8s-<slug>`,
 * `env-<slug>`), and some fleets name the resident after the realm instead.
 */
function keepsRealm(valkyrie: ValkyrieResidentView, slug: string): boolean {
  if (!slug) return false;
  if (valkyrie.name === slug) return true;
  return valkyrie.environmentId === slug || valkyrie.environmentId.endsWith(`-${slug}`);
}

/** The Valkyrie resident behind a ravn, or null when the fleets do not line up. */
export function valkyrieForRavn(
  valkyries: readonly ValkyrieResidentView[],
  ravn: Pick<Ravn, 'residentName' | 'personaName'>,
): ValkyrieResidentView | null {
  const slug = realmSlugForRavn(ravn);
  return valkyries.find((valkyrie) => keepsRealm(valkyrie, slug)) ?? null;
}

/** The realm a resident keeps, but only when the governance service lists it. */
export function realmForRavn(
  realms: readonly RealmView[],
  ravn: Pick<Ravn, 'residentName' | 'personaName'>,
): RealmView | null {
  const slug = realmSlugForRavn(ravn);
  if (!slug) return null;
  return realms.find((realm) => realm.slug === slug) ?? null;
}

/** Reviews waiting on the environment this resident keeps. */
export function pendingReviewsFor(
  reviews: readonly PendingReviewView[],
  valkyrie: ValkyrieResidentView | null,
): PendingReviewView[] {
  if (!valkyrie) return [];
  return reviews.filter((review) => review.environmentId === valkyrie.environmentId);
}

function actionTime(action: ValkyrieActionView): string {
  return action.finishedAt ?? action.startedAt ?? '';
}

/** The most recent thing this resident did, when the dashboard reports actions. */
export function latestActionFor(
  actions: readonly ValkyrieActionView[],
  valkyrie: ValkyrieResidentView | null,
): ValkyrieActionView | null {
  if (!valkyrie) return null;
  const mine = actions.filter((action) => action.ownerValkyrieId === valkyrie.id);
  if (mine.length === 0) return null;
  return mine.reduce((latest, action) =>
    actionTime(action) > actionTime(latest) ? action : latest,
  );
}

/**
 * One line of what the resident last did. Null when nothing on record says —
 * the card then shows no activity line rather than inventing one.
 */
export function residentActivity(
  ravn: Pick<Ravn, 'updatedAt'>,
  valkyrie: ValkyrieResidentView | null,
  action: ValkyrieActionView | null,
): ResidentActivity | null {
  if (action) return { label: action.title, at: actionTime(action) || null };
  if (valkyrie?.lastActionAt) return { label: 'last acted', at: valkyrie.lastActionAt };
  if (ravn.updatedAt) return { label: 'last seen', at: ravn.updatedAt };
  return null;
}

/** Residents that need an answer come first, then the awake ones, then by name. */
export function orderResidents(ravens: readonly Ravn[], needsYou: (ravn: Ravn) => number): Ravn[] {
  return [...ravens].sort((left, right) => {
    const waiting = (needsYou(right) > 0 ? 1 : 0) - (needsYou(left) > 0 ? 1 : 0);
    if (waiting !== 0) return waiting;
    const active = (right.status === 'active' ? 1 : 0) - (left.status === 'active' ? 1 : 0);
    if (active !== 0) return active;
    return nameForRavn(left).localeCompare(nameForRavn(right));
  });
}

/** How many residents run a given persona. */
export function personaUsage(ravens: readonly Ravn[], personaName: string): number {
  return ravens.filter((ravn) => ravn.personaName === personaName).length;
}
