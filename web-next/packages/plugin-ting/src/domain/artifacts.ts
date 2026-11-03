import type { WorkSummary } from './work';
import { REVISION_PENDING, type RunReview } from './review';

/**
 * Artifacts are what agents produce in a workflow run, read from the outcomes the run
 * already records in its session log. An artifact is one agent's multi-line output
 * field (a plan, a spec, the details of a code change); each time that agent reports
 * the field again is a new version, and what the other agents say in between is its
 * history. Nothing is stored for this: the run's log is the record.
 */

/** One `room_outcome` from a run's session log. */
export interface RunOutcome {
  seq: number;
  at: string;
  eventType: string;
  participantId: string;
  /** `ravn` for an agent; `skuld` posts the brief and `workflow` decides the run. */
  participantType: string;
  fields: Record<string, unknown>;
}

export interface ArtifactVersion {
  number: number;
  seq: number;
  at: string;
  text: string;
  eventType: string;
  verdict: string | null;
}

/** What another participant said about the work, in order. */
export interface ArtifactFeedback {
  seq: number;
  at: string;
  from: string;
  eventType: string;
  verdict: string | null;
  summary: string;
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
  /** `<run slug>/<producer>/<field>`, stable across versions. */
  id: string;
  run: RunRef;
  field: string;
  title: string;
  /** What the run was asked to do, in one line: its brief's first line, or the run's title. */
  subject: string;
  producerId: string;
  producer: string;
  versions: ArtifactVersion[];
  feedback: ArtifactFeedback[];
  status: ArtifactStatus;
  updatedAt: string;
}

export const ARTIFACT_STATUS_LABEL: Record<ArtifactStatus, string> = {
  waiting: 'Waiting on you',
  inProgress: 'In progress',
  done: 'Done',
  failed: 'Run failed',
};

const AGENT_PARTICIPANT = 'ravn';

function humanize(value: string): string {
  const words = value.replace(/[_.-]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** `flock-developer-analyst` reads as "Developer analyst". */
export function producerName(participantId: string): string {
  return humanize(participantId.replace(/^flock-/, ''));
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/** Every outcome carries a one-line summary of itself; it describes the work, it is not it. */
const SUMMARY_FIELD = 'summary';

/**
 * The documents in an outcome: its multi-line text, or, when it has none, its longest
 * text. Identifiers and verdicts are single words and never count.
 */
function documentFields(fields: Record<string, unknown>): string[] {
  const prose = Object.entries(fields)
    .filter(([name]) => name !== SUMMARY_FIELD)
    .filter((entry): entry is [string, string] => typeof entry[1] === 'string')
    .map(([name, value]) => [name, value.trim()] as const)
    .filter(([, value]) => /\s/.test(value));
  const multiLine = prose.filter(([, value]) => value.includes('\n')).map(([name]) => name);
  if (multiLine.length > 0) return multiLine;
  const longest = [...prose].sort((left, right) => right[1].length - left[1].length)[0];
  return longest ? [longest[0]] : [];
}

function verdictOf(fields: Record<string, unknown>): string | null {
  return text(fields.verdict) || null;
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

/** Every artifact a run's agents produced, newest version last in each. */
export function artifactsFromOutcomes(
  run: RunRef,
  outcomes: readonly RunOutcome[],
  waitingSlugs: ReadonlySet<string>,
): Artifact[] {
  const ordered = [...outcomes].sort((left, right) => left.seq - right.seq);
  const subject =
    runBrief(ordered)
      .split('\n')
      .map((line) => line.trim())
      .find(Boolean) ?? run.title;
  const byId = new Map<string, Artifact>();
  for (const outcome of ordered) {
    if (outcome.participantType !== AGENT_PARTICIPANT) continue;
    for (const field of documentFields(outcome.fields)) {
      const id = `${run.slug}/${outcome.participantId}/${field}`;
      const artifact = byId.get(id) ?? {
        id,
        run,
        field,
        title: humanize(field),
        subject,
        producerId: outcome.participantId,
        producer: producerName(outcome.participantId),
        versions: [],
        feedback: [],
        status: artifactStatus(run, waitingSlugs),
        updatedAt: outcome.at,
      };
      artifact.versions.push({
        number: artifact.versions.length + 1,
        seq: outcome.seq,
        at: outcome.at,
        text: text(outcome.fields[field]),
        eventType: outcome.eventType,
        verdict: verdictOf(outcome.fields),
      });
      artifact.updatedAt = outcome.at;
      byId.set(id, artifact);
    }
  }
  for (const artifact of byId.values()) {
    const first = artifact.versions[0]?.seq ?? 0;
    artifact.feedback = ordered
      .filter((outcome) => outcome.seq > first && outcome.participantId !== artifact.producerId)
      .filter((outcome) => outcome.participantType !== 'skuld')
      .map((outcome) => ({
        seq: outcome.seq,
        at: outcome.at,
        from:
          outcome.participantType === AGENT_PARTICIPANT
            ? producerName(outcome.participantId)
            : 'Workflow',
        eventType: outcome.eventType,
        verdict: verdictOf(outcome.fields),
        summary: text(outcome.fields.summary),
      }));
  }
  return [...byId.values()];
}

const PROMPT_HEADING = '## Prompt';

/**
 * What the person asked for. Ting wraps a launch's prompt in a brief with sections
 * (`_build_workflow_initiative_context` in `src/ting/api/workflows.py`); the person's
 * own words are its Prompt section.
 */
export function launchPrompt(brief: string): string {
  const lines = brief.split('\n');
  const start = lines.findIndex((line) => line.trim() === PROMPT_HEADING);
  if (start < 0) return brief.trim();
  const rest = lines.slice(start + 1);
  const end = rest.findIndex((line) => line.startsWith('## '));
  return (end < 0 ? rest : rest.slice(0, end)).join('\n').trim();
}

/** What the run was started to do, from the dispatch outcome. */
export function runBrief(outcomes: readonly RunOutcome[]): string {
  const dispatch = outcomes.find((outcome) => outcome.participantType === 'skuld');
  if (!dispatch) return '';
  return launchPrompt(text(dispatch.fields.prompt) || text(dispatch.fields.task_description));
}

/** Feedback that arrived after one version and before the next. */
export function feedbackFor(artifact: Artifact, version: ArtifactVersion): ArtifactFeedback[] {
  const next = artifact.versions.find((item) => item.number === version.number + 1);
  return artifact.feedback.filter(
    (item) => item.seq > version.seq && (next === undefined || item.seq < next.seq),
  );
}

/** What a new run is asked to do when a finished artifact is sent back for revision. */
export function revisionPrompt(
  artifact: Artifact,
  version: ArtifactVersion,
  brief: string,
  notes: string,
): string {
  return [
    brief.trim(),
    `Revise the ${artifact.title.toLowerCase()} below (version ${version.number} from run ${artifact.run.slug}) to address the reviewer's notes. Keep what the notes do not question.`,
    `Reviewer's notes:\n${notes.trim()}`,
    `Previous ${artifact.title.toLowerCase()}:\n${version.text}`,
  ]
    .filter(Boolean)
    .join('\n\n');
}

export function newestFirst(artifacts: readonly Artifact[]): Artifact[] {
  return [...artifacts].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
}

/** A gate the run is waiting on, which an artifact's latest version is answered through. */
export interface PendingGate {
  id: string;
  activationId: string;
}

/**
 * An artifact in the shape the review workspace reads, so it is reviewed on the same
 * page, with the same notes and marks, as a gate's own document.
 */
export function artifactReview(artifact: Artifact, gate: PendingGate | null): RunReview {
  const latest = artifact.versions.length;
  return {
    columnId: artifact.id,
    title: artifact.title,
    review: null,
    workflowName: `${artifact.producer} · ${artifact.run.title}`,
    taskId: artifact.run.slug,
    sessionId: artifact.run.sessionId,
    panels: [
      {
        widget: 'document',
        title: artifact.title,
        source: { kind: 'outcome', value: artifact.field },
      },
    ],
    revisions: artifact.versions.map((version) => {
      const answersGate = gate !== null && version.number === latest;
      return {
        number: version.number,
        gateId: answersGate ? gate.id : `${artifact.id}#${version.number}`,
        activationId: answersGate ? gate.activationId : '',
        requestedAt: version.at,
        status: answersGate ? REVISION_PENDING : humanize(version.verdict ?? 'reported'),
        summary: '',
        decidedBy: '',
        notes: feedbackFor(artifact, version)
          .map(
            (item) =>
              `${item.from}${item.verdict ? ` · ${humanize(item.verdict)}` : ''}${item.summary ? `: ${item.summary}` : ''}`,
          )
          .join('\n'),
        decidedAt: '',
        fields: { [artifact.field]: version.text },
      };
    }),
  };
}

/** Fields a gate's own review already shows, which need no second tab. */
export function documentFieldsShown(reviews: readonly RunReview[]): Set<string> {
  return new Set(
    reviews
      .flatMap((review) => review.panels)
      .filter((panel) => panel.widget === 'document' && panel.source?.kind === 'outcome')
      .map((panel) => panel.source!.value),
  );
}
