import type { WorkSummary } from './work';
import { REVISION_PENDING, type ReviewRevision, type RunReview } from './review';

/**
 * Artifacts are the documents sessions present (a plan, a spec, a report, an image),
 * as Forge records them: one artifact publication per presented file
 * (`GET /artifact-publications`). Files presented under one artifact key are one
 * artifact, each file a version of it; in a workflow run, Ting's gate reviews name the
 * version a person decided on. Outcome fields stay in a run's log as its record and are
 * not listed here.
 */

/** One Forge artifact publication of a presented file. */
export interface Publication {
  seq: number;
  sessionId: string;
  providerKey: string;
  revisionId: string;
  /** The presented file's own id, which Forge serves it by. */
  fileId: string;
  title: string | null;
  url: string | null;
  at: string;
  mime: string | null;
  versionLabel: string | null;
}

export interface ArtifactVersion {
  number: number;
  revisionId: string;
  fileId: string;
  label: string;
  at: string;
  name: string;
  mime: string | null;
  url: string | null;
}

/** The run conditions that end a run: nothing more will happen in it. */
export const RUN_ENDED: ReadonlySet<string> = new Set(['completed', 'failed', 'canceled']);

export type ArtifactStatus = 'waiting' | 'inProgress' | 'done' | 'failed' | 'canceled';

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
  /** Every session the run worked in: one per member of a step-engine run. */
  sessions: readonly RunSession[];
}

/** A session a run worked in, and the member whose steps it ran (empty for the run's own). */
export interface RunSession {
  sessionId: string;
  instanceId: string | null;
  member: string;
}

/** The Forge session that presented an artifact. */
export interface SessionRef {
  sessionId: string;
  instanceId: string | null;
  title: string;
  /** Notes reach it as a chat message only while it runs. */
  running: boolean;
  /** The workflow run the session works for, and its role there, when it is one. */
  runSlug?: string;
  member?: string;
}

export interface Artifact {
  /** Forge's `provider_key`: stable across versions. */
  id: string;
  /** The agent's own name for it (`plan`), or the file's id when it was presented once. */
  key: string;
  /** Presented under an artifact key, so later files under it are its versions. */
  keyed: boolean;
  session: SessionRef;
  /** The workflow run the session belongs to, when it is one. */
  run: RunRef | null;
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
  canceled: 'Run canceled',
};

function humanize(value: string): string {
  const words = value.replace(/[_.-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const BLOCKED = 'blocked';

/**
 * The run is held at a gate a person answers. Only a blocked run waits: finished runs
 * keep their last attention note, so it says nothing about now. A run blocked with no
 * decision asked of anyone (a session that never started) waits on no one.
 */
export function runWaits(run: RunRef, waitingSlugs: ReadonlySet<string>): boolean {
  return (
    waitingSlugs.has(run.slug) || (run.status === BLOCKED && run.attention?.kind === 'decision')
  );
}

export function artifactStatus(run: RunRef, waitingSlugs: ReadonlySet<string>): ArtifactStatus {
  if (runWaits(run, waitingSlugs)) return 'waiting';
  if (run.condition === 'failed') return 'failed';
  if (run.condition === 'canceled') return 'canceled';
  if (run.condition === 'completed') return 'done';
  return 'inProgress';
}

export function runRef(item: WorkSummary): RunRef | null {
  if (!item.session || !item.campaignSlug) return null;
  return runWithSessions(item, [
    { sessionId: item.session.id, instanceId: item.session.connectionId, member: '' },
  ]);
}

/**
 * A run with the sessions its review read from its ledger. A step-engine run has no
 * session of its own, so its first session stands for it where one session is asked for.
 */
export function runWithSessions(item: WorkSummary, sessions: readonly RunSession[]): RunRef | null {
  if (!item.campaignSlug) return null;
  const first = sessions[0] ?? null;
  return {
    slug: item.campaignSlug,
    title: item.title,
    status: item.rawState.status,
    condition: item.condition,
    attention: item.attention,
    sessionId: first?.sessionId ?? '',
    instanceId: first?.instanceId ?? null,
    workflowId: item.workflow?.id ?? null,
    workflowVersion: item.workflow?.version ?? null,
    updatedAt: item.updatedAt,
    sessions,
  };
}

/** A keyed file's provider key is `<session>:<key>`; an unkeyed one is the file's own id. */
function artifactKey(publication: Publication): { key: string; keyed: boolean } {
  const prefix = `${publication.sessionId}:`;
  return publication.providerKey.startsWith(prefix)
    ? { key: publication.providerKey.slice(prefix.length), keyed: true }
    : { key: publication.providerKey, keyed: false };
}

function runSession(run: RunRef, session: RunSession): SessionRef {
  return {
    sessionId: session.sessionId,
    instanceId: session.instanceId,
    title: session.member ? `${run.title} (${session.member})` : run.title,
    running: !RUN_ENDED.has(run.condition),
  };
}

/** Every artifact a run's agents presented, in every session it worked in, oldest first. */
export function artifactsFromPublications(
  run: RunRef,
  publications: readonly Publication[],
  waitingSlugs: ReadonlySet<string>,
): Artifact[] {
  const status = artifactStatus(run, waitingSlugs);
  return run.sessions.flatMap((session) =>
    collect(runSession(run, session), run, status, publications),
  );
}

/** Every artifact a session that is no workflow run presented, versions oldest first. */
export function sessionArtifacts(
  session: SessionRef,
  publications: readonly Publication[],
): Artifact[] {
  return collect(session, null, session.running ? 'inProgress' : 'done', publications);
}

function collect(
  session: SessionRef,
  run: RunRef | null,
  status: ArtifactStatus,
  publications: readonly Publication[],
): Artifact[] {
  const byKey = new Map<string, Artifact>();
  const ordered = publications
    .filter((publication) => publication.sessionId === session.sessionId)
    .sort((left, right) => left.seq - right.seq);
  for (const publication of ordered) {
    const { key, keyed } = artifactKey(publication);
    const name = publication.title ?? key;
    const artifact = byKey.get(publication.providerKey) ?? {
      id: publication.providerKey,
      key,
      keyed,
      session,
      run,
      title: keyed ? humanize(key) : name,
      versions: [],
      status,
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
      fileId: publication.fileId,
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

const TEXT_TYPES = /^(text\/|application\/(json|x-yaml|yaml|xml|markdown))/i;

/** What kind of document an artifact is: its key's name, or its file type when unkeyed. */
export function artifactKind(artifact: Artifact): string {
  const latest = artifact.versions[artifact.versions.length - 1];
  if (!latest || artifact.keyed) return artifact.title;
  const mime = latest.mime ?? '';
  if (mime.startsWith('image/')) return 'Image';
  if (mime === 'application/pdf') return 'PDF';
  if (TEXT_TYPES.test(mime)) return 'Document';
  return 'File';
}

export function newestFirst<T extends { updatedAt: string }>(artifacts: readonly T[]): T[] {
  return [...artifacts].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

/** Whether a version is read on the page; anything else opens as a file. */
export function isReadable(version: ArtifactVersion): boolean {
  return TEXT_TYPES.test(version.mime ?? 'text/plain');
}

/** The panel a presented file that is not text is shown in: it opens the file itself. */
export const PRESENTED_FILE_WIDGET = 'presentedFile';

/** Where a version's file name and type sit beside its file id in a revision's fields. */
export function presentedFileName(field: string): string {
  return `${field}.name`;
}

export function presentedFileMime(field: string): string {
  return `${field}.mime`;
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
    `Revise the ${name} below (${version.label} from run ${artifact.run?.slug ?? artifact.session.title}) to address the reviewer's notes. Keep what the notes do not question. Present the revision under artifact key ${artifact.key}.`,
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
  const latest = artifact.versions[artifact.versions.length - 1];
  // A text document is read on the page; any other file opens in the file preview.
  const readable = latest === undefined || isReadable(latest);
  return {
    columnId: artifact.id,
    title: artifact.title,
    review: null,
    workflowName: artifact.session.title,
    taskId: artifact.run?.slug ?? artifact.session.sessionId,
    sessionId: artifact.session.sessionId,
    panels: [
      {
        widget: readable ? 'document' : PRESENTED_FILE_WIDGET,
        title: artifact.title,
        source: { kind: 'outcome', value: artifact.key },
      },
    ],
    revisions: artifact.versions.map((version) => {
      const gate = deciding(artifact, version, gates);
      const fields = readable
        ? { [artifact.key]: versionDocument(version, texts.get(version.revisionId)) }
        : {
            [artifact.key]: version.fileId,
            [presentedFileName(artifact.key)]: version.name,
            // Only a typed file is not read as text.
            [presentedFileMime(artifact.key)]: version.mime ?? '',
          };
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
        fields,
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

/**
 * Artifact keys a gate's own document panels already show, which need no second tab. A gate
 * shows the document under its field's name whether or not its revision links the presented
 * file: a plan presented as `plan` is the plan the gate asks about, and a second tab without
 * the gate's decision would be the one a person opens.
 */
export function artifactKeysShown(
  reviews: readonly RunReview[],
  presented: readonly Pick<Artifact, 'key'>[],
): Set<string> {
  const fields = new Set(
    reviews
      .flatMap((review) => review.panels)
      .filter((panel) => panel.widget === 'document' && panel.source?.kind === 'outcome')
      .map((panel) => panel.source!.value),
  );
  const linked = reviews
    .flatMap((review) => review.revisions)
    .flatMap((revision) => revision.artifacts ?? [])
    .map((artifact) => artifact.artifactKey);
  return new Set(
    [...linked, ...presented.map((artifact) => artifact.key)].filter((key) => fields.has(key)),
  );
}
