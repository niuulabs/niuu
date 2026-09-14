/**
 * Naming conventions that bind a realm to the objects the create recipe makes.
 *
 * The realm itself has no free-form field, so every part is found again by name:
 * the resident is named after the realm, the persona is `realm-<slug>`, the memory
 * mount is `realm-<slug>` on its deployment target, and the charter page lives under
 * `realms/<slug>/`.
 */

export const SLUG_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;

export function personaNameFor(slug: string): string {
  return `realm-${slug}`;
}

export function residentNameFor(slug: string): string {
  return slug;
}

/**
 * Mímir lists a deployed instance under its bare deployment name, whatever target it
 * runs on (verified against the cluster target on yggdrasil).
 */
export function mountNameFor(slug: string): string {
  return `realm-${slug}`;
}

export function deploymentNameFor(slug: string): string {
  return `realm-${slug}`;
}

export function routingRuleIdFor(slug: string): string {
  return `realm-${slug}`;
}

export function routingPrefixFor(slug: string): string {
  return `realms/${slug}/`;
}

export function charterPagePathFor(slug: string): string {
  return `realms/${slug}/charter.md`;
}

export function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100);
}

export function isValidSlug(slug: string): boolean {
  return SLUG_PATTERN.test(slug);
}

/** Trust ladder, in order. `mutate` never gets a grant from the easy path. */
export const ACTION_CLASSES = [
  'observe',
  'draft',
  'build',
  'test',
  'deploy',
  'mutate',
  'spend',
] as const;
export type ActionClass = (typeof ACTION_CLASSES)[number];

/** Three-way trust setting shown to people; maps onto grant levels. */
export type TrustSetting = 'auto' | 'ask' | 'never';

export const TRUST_LEVEL_FOR: Record<TrustSetting, number | null> = {
  auto: 2,
  ask: 1,
  never: null,
};

export function trustSettingForLevel(level: number | null | undefined): TrustSetting {
  if (level === null || level === undefined) return 'never';
  return level >= 2 ? 'auto' : 'ask';
}

export type TrustPreset = Record<ActionClass, TrustSetting>;

export const BALANCED_TRUST: TrustPreset = {
  observe: 'auto',
  draft: 'auto',
  build: 'auto',
  test: 'auto',
  deploy: 'ask',
  mutate: 'never',
  spend: 'ask',
};

export const ACTION_CLASS_COPY: Record<ActionClass, string> = {
  observe: 'read code, tickets, CI, logs',
  draft: 'write plans, comments, docs',
  build: 'run sessions, edit code, open pull requests',
  test: 'run test suites and the QA loop',
  deploy: 'merge to dev, deploy to dev',
  mutate: 'change infra, migrations, production',
  spend: 'exceed the weekly budget',
};

/** What the observe grant's `limits` carries: where the realm looks. */
export interface RealmBinding {
  template: string;
  repo: string;
  branch: string;
  trackerBoard: string;
  bugBoard?: string;
  mountTarget: string;
}

/** Everything the easy path needs to create a realm. */
export interface RealmDraft {
  slug: string;
  name: string;
  templateId: string;
  charter: string;
  repo: string;
  branch: string;
  trackerBoard: string;
  bugBoard: string;
  integrationIds: string[];
  mountTarget: string;
  profileId: string;
  instanceId: string;
  model: string;
  trust: TrustPreset;
}

export const EMPTY_DRAFT: RealmDraft = {
  slug: '',
  name: '',
  templateId: 'product-resident',
  charter: '',
  repo: '',
  branch: '',
  trackerBoard: '',
  bugBoard: '',
  integrationIds: [],
  mountTarget: '',
  profileId: '',
  instanceId: '',
  model: '',
  trust: BALANCED_TRUST,
};

/** The realm page's projection over the objects the recipe created. */
export interface RealmView {
  slug: string;
  name: string;
  autonomyProfile: string;
  instanceId: string | null;
  binding: RealmBinding | null;
  personaName: string;
  residentName: string;
  environmentId: string | null;
  wakefulness: string | null;
  autonomyMode: string | null;
  confidence: number | null;
  residentStatus: string | null;
  ravnId: string | null;
  grants: Array<{ actionClass: string; level: number; target: string }>;
  pendingReviews: number;
  runningSessions: number;
}

export function requiredDraftError(draft: RealmDraft, needsBoard: boolean): string | null {
  if (!isValidSlug(draft.slug)) {
    return 'Give the realm a name made of lowercase letters, digits, dashes or underscores.';
  }
  if (!draft.name.trim()) return 'Give the realm a name.';
  if (!draft.charter.trim()) return 'Write a charter: a few sentences about what matters.';
  if (!draft.repo) return 'Pick a repository.';
  if (needsBoard && !draft.trackerBoard) return 'Pick the tracker board the resident works from.';
  if (!draft.profileId || !draft.instanceId) return 'Pick where the resident runs.';
  if (!draft.mountTarget) return 'Pick where the realm memory is created.';
  return null;
}
