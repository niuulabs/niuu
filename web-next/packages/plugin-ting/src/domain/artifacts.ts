import type { WorkSummary } from './work';
import { REVISION_PENDING, type ReviewRevision, type RunReview } from './review';

/**
 * Artifacts are the documents agents present in a workflow run (a plan, a spec, a
 * report), as Forge records them: one artifact publication per presented file
 * (`GET /artifact-publications`). Files an agent presents under one artifact key are
 * one artifact, each file a version of it; Ting's gate reviews name the version a
 * person decided on. Outcome fields stay in the run's log as its record and are not
 * listed here.
 */

/** One Forge artifact publication of a presented file. */
export interface Publication {
  seq: number;
  sessionId: string;
  providerKey: string;
  revisionId: string;
  title: string | null;
  url: string | null;
  at: string;
  mime: string | null;
  versionLabel: string | null;
}

export interface ArtifactVersion {
  number: number;
  revisionId: string;
  label: string;
  at: string;
  name: string;
  mime: string | null;
  url: string | null;
}

export type ArtifactStatus = 'waiting' | 'inProgress' | 'done' | 'failed';

export interface RunRef {
  slug: string;
  title: string;
  status: string;
  condition: WorkSummary['condition'];
  attention: WorkSummary['attention'];
  sessionId: string;
  instanceId: string | null;
  workflowId: string | null;
  workflowVersion: string | null;
  updatedAt: string | null;
}

export interface Artifact {
  /** Forge's `provider_key`: stable across versions. */
  id: string;
  /** The agent's own name for it (`plan`), or the file's id when it was presented once. */
  key: string;
  run: RunRef;
  title: string;
  versions: ArtifactVersion[];
  status: ArtifactStatus;
  updatedAt: string;
}

export const ARTIFACT_STATUS_LABEL: Record<ArtifactStatus, string> = {
  waiting: 'Waiting on you',
  inProgress: 'In progress',
  done: 'Done',
  failed: 'Run failed',
};

function humanize(value: string): string {
  const words = value.replace(/[_.-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const BLOCKED = 'blocked';

/**
 * The run is held at a gate a person answers. Only a blocked run waits: finished runs
 * keep their last attention note, so it says nothing about now.
 */
function runWaits(run: RunRef, waitingSlugs: ReadonlySet<string>): boolean {
  return waitingSlugs.has(run.slug) || run.status === BLOCKED;
}

export function artifactStatus(run: RunRef, waitingSlugs: ReadonlySet<string>): ArtifactStatus {
  if (runWaits(run, waitingSlugs)) return 'waiting';
  if (run.condition === 'failed') return 'failed';
  if (run.condition === 'completed') return 'done';
  return 'inProgress';
}

export function runRef(item: WorkSummary): RunRef | null {
  if (!item.session || !item.campaignSlug) return null;
  return {
    slug: item.campaignSlug,
    title: item.title,
    status: item.rawState.status,
    condition: item.condition,
    attention: item.attention,
    sessionId: item.session.id,
    instanceId: item.session.connectionId,
    workflowId: item.workflow?.id ?? null,
    workflowVersion: item.workflow?.version ?? null,
    updatedAt: item.updatedAt,
  };
}

/** A keyed file's provider key is `<session>:<key>`; an unkeyed one is the file's own id. */
function artifactKey(publication: Publication): string {
  const prefix = `${publication.sessionId}:`;
  return publication.providerKey.startsWith(prefix)
    ? publication.providerKey.slice(prefix.length)
    : publication.providerKey;
}

/** Every artifact a run's agents presented, versions oldest first. */
export function artifactsFromPublications(
  run: RunRef,
  publications: readonly Publication[],
  waitingSlugs: ReadonlySet<string>,
): Artifact[] {
  const byKey = new Map<string, Artifact>();
  const ordered = publications
    .filter((publication) => publication.sessionId === run.sessionId)
    .sort((left, right) => left.seq - right.seq);
  for (const publication of ordered) {
    const key = artifactKey(publication);
    const name = publication.title ?? key;
    const artifact = byKey.get(publication.providerKey) ?? {
      id: publication.providerKey,
      key,
      run,
      title: key === publication.revisionId ? name : humanize(key),
      versions: [],
      status: artifactStatus(run, waitingSlugs),
      updatedAt: publication.at,
    };
    // A republish of the same file is the same version.
    if (artifact.versions.some((version) => version.revisionId === publication.revisionId)) {
      continue;
    }
    const number = artifact.versions.length + 1;
    artifact.versions.push({
      number,
      revisionId: publication.revisionId,
      label: publication.versionLabel ?? `v${number}`,
      at: publication.at,
      name,
      mime: publication.mime,
      url: publication.url,
    });
    artifact.updatedAt = publication.at;
    byKey.set(publication.providerKey, artifact);
  }
  return [...byKey.values()];
}

export function newestFirst<T extends { updatedAt: string }>(artifacts: readonly T[]): T[] {
  return [...artifacts].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

const TEXT_TYPES = /^(text\/|application\/(json|x-yaml|yaml|xml|markdown))/i;

/** Whether a version is read on the page; anything else opens as a file. */
export function isReadable(version: ArtifactVersion): boolean {
  return TEXT_TYPES.test(version.mime ?? 'text/plain');
}

/** What the review page shows for a version: its text, or a link to open the file. */
export function versionDocument(version: ArtifactVersion, text: string | undefined): string {
  if (isReadable(version) && text !== undefined) return text;
  return version.url ? `[Open ${version.name}](${version.url})` : version.name;
}

/** What a new run is asked to do when a finished artifact is sent back for revision. */
export function revisionPrompt(
  artifact: Artifact,
  version: ArtifactVersion,
  document: string,
  brief: string,
  notes: string,
): string {
  const name = artifact.title.toLowerCase();
  return [
    brief.trim(),
    `Revise the ${name} below (${version.label} from run ${artifact.run.slug}) to address the reviewer's notes. Keep what the notes do not question. Present the revision under artifact key ${artifact.key}.`,
    `Reviewer's notes:\n${notes.trim()}`,
    `Previous ${name}:\n${document}`,
  ]
    .filter(Boolean)
    .join('\n\n');
}

/** The gate revision that put this exact version in front of a person, if one did. */
function deciding(
  artifact: Artifact,
  version: ArtifactVersion,
  gates: readonly RunReview[],
): ReviewRevision | null {
  for (const review of gates) {
    for (const revision of review.revisions) {
      const shown = revision.artifacts ?? [];
      if (
        shown.some(
          (item) => item.providerKey === artifact.id && item.revisionId === version.revisionId,
        )
      ) {
        return revision;
      }
    }
  }
  return null;
}

/**
 * An artifact in the shape the review workspace reads, so it is reviewed on the same
 * page, with the same notes and marks, as a gate's own document. A version a gate
 * showed carries that gate's decision; the pending one answers the gate.
 */
export function artifactReview(
  artifact: Artifact,
  gates: readonly RunReview[],
  texts: ReadonlyMap<string, string>,
): RunReview {
  return {
    columnId: artifact.id,
    title: artifact.title,
    review: null,
    workflowName: artifact.run.title,
    taskId: artifact.run.slug,
    sessionId: artifact.run.sessionId,
    panels: [
      {
        widget: 'document',
        title: artifact.title,
        source: { kind: 'outcome', value: artifact.key },
      },
    ],
    revisions: artifact.versions.map((version) => {
      const gate = deciding(artifact, version, gates);
      const document = versionDocument(version, texts.get(version.revisionId));
      return {
        number: version.number,
        gateId: gate?.gateId ?? `${artifact.id}#${version.number}`,
        activationId: gate?.activationId ?? '',
        requestedAt: version.at,
        status: gate?.status ?? 'Presented',
        summary: version.label,
        decidedBy: gate?.decidedBy ?? '',
        notes: gate?.notes ?? '',
        decidedAt: gate?.decidedAt ?? '',
        fields: { [artifact.key]: document },
      };
    }),
  };
}

/** Whether any of these gate revisions is the pending one. */
export function answersPendingGate(review: RunReview): boolean {
  return review.revisions.some((revision) => revision.status === REVISION_PENDING);
}

/**
 * A gate's document panel reads the version the gate showed of the artifact presented
 * under that panel's field, instead of the copy in the agent's outcome; a gate that
 * showed no such version keeps its outcome field.
 */
export function gatesWithArtifacts(
  gates: readonly RunReview[],
  artifacts: readonly Artifact[],
  texts: ReadonlyMap<string, string>,
): RunReview[] {
  const versions = new Map(
    artifacts.flatMap((artifact) =>
      artifact.versions.map((version) => [`${artifact.id}|${version.revisionId}`, version]),
    ),
  );
  return gates.map((review) => ({
    ...review,
    revisions: review.revisions.map((revision) => {
      const fields = { ...revision.fields };
      for (const shown of revision.artifacts ?? []) {
        const version = versions.get(`${shown.providerKey}|${shown.revisionId}`);
        if (!version) continue;
        fields[shown.artifactKey] = versionDocument(version, texts.get(version.revisionId));
      }
      return { ...revision, fields };
    }),
  }));
}

/** Artifact keys a gate's own document panels already show, which need no second tab. */
export function artifactKeysShown(reviews: readonly RunReview[]): Set<string> {
  const fields = new Set(
    reviews
      .flatMap((review) => review.panels)
      .filter((panel) => panel.widget === 'document' && panel.source?.kind === 'outcome')
      .map((panel) => panel.source!.value),
  );
  const keys = reviews
    .flatMap((review) => review.revisions)
    .flatMap((revision) => revision.artifacts ?? [])
    .map((artifact) => artifact.artifactKey);
  return new Set(keys.filter((key) => fields.has(key)));
}
