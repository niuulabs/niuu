/**
 * A persona as its document: the YAML its author writes, and exactly what is saved.
 *
 * The wizard edits a few fields of this document and leaves every other field as it is,
 * so a persona written in YAML (or copied from a built-in) keeps what the wizard does not
 * show. The YAML editor edits the same document as text.
 */
import { dump, load } from 'js-yaml';
import type { PersonaSummary } from '@niuulabs/domain';
import type { PersonaDocument } from '../ports';

/** What a persona may change, as the harness on a workflow step enforces it. */
export const PERMISSIONS = [
  { value: 'read_only', label: 'Read only', hint: 'Looks at the code, never changes it' },
  {
    value: 'workspace_write',
    label: 'Change the workspace',
    hint: 'Edits files and commits in its own copy',
  },
  { value: 'full_access', label: 'Full access', hint: 'Everything the harness allows' },
] as const;

/** One way a persona can finish a step: the outcome it reports, the event that sends, and
 *  the persona's rule for when to report it. */
export interface PersonaOutcome {
  verdict: string;
  event: string;
  when: string;
}

/** Names a server accepts for a persona (letters, digits, dashes, underscores). */
export const DOCUMENT_NAME_PATTERN = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/;
const VERDICT_PATTERN = /^[a-z0-9][a-z0-9_]*$/;
const EVENT_PATTERN = /^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$/;

/** The order fields are written in, so the YAML reads the way the wizard asks. */
const FIELD_ORDER = [
  'name',
  'description',
  'permission_mode',
  'system_prompt_template',
  'consumes',
  'produces',
];

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/** Events a persona's outcomes send start with this, unless its author chose others. */
export function eventPrefix(name: string): string {
  const prefix = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return prefix || 'persona';
}

/** An outcome name as typed, made into one: lowercase words joined by underscores. */
export function outcomeName(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

/** An event name as typed, made into one: lowercase words joined by dots. */
export function eventName(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, '_')
    .replace(/^[._]+|[._]+$/g, '');
}

/** A new persona: one outcome, nothing it starts on, and no instructions yet. */
export function blankPersonaDocument(): PersonaDocument {
  const prefix = eventPrefix('');
  return {
    name: '',
    description: '',
    permission_mode: 'read_only',
    system_prompt_template: '',
    consumes: { event_types: [] },
    produces: {
      event_type: `${prefix}.completed`,
      event_type_map: { done: `${prefix}.done` },
      when: {},
      schema: {
        verdict: { type: 'enum', values: ['done'], description: 'the outcome' },
        summary: { type: 'string', description: 'one-line summary' },
      },
    },
  };
}

/** Another persona's document as the start of a new one: everything but its name. */
export function startFrom(source: PersonaDocument, current: PersonaDocument): PersonaDocument {
  const copy = structuredClone(source);
  copy.name = text(current.name);
  return ordered(copy);
}

export function documentName(doc: PersonaDocument): string {
  return text(doc.name);
}

export function documentDescription(doc: PersonaDocument): string {
  return text(doc.description);
}

export function documentInstructions(doc: PersonaDocument): string {
  return text(doc.system_prompt_template);
}

export function documentPermission(doc: PersonaDocument): string {
  return text(doc.permission_mode);
}

export function startsOn(doc: PersonaDocument): string[] {
  const events = record(doc.consumes).event_types;
  return Array.isArray(events) ? events.map(String) : [];
}

export function outcomesOf(doc: PersonaDocument): PersonaOutcome[] {
  const produces = record(doc.produces);
  const map = record(produces.event_type_map);
  const when = record(produces.when);
  return Object.entries(map).map(([verdict, event]) => ({
    verdict,
    event: String(event),
    when: text(when[verdict]),
  }));
}

function ordered(doc: PersonaDocument): PersonaDocument {
  const result: PersonaDocument = {};
  for (const key of FIELD_ORDER) if (key in doc) result[key] = doc[key];
  for (const [key, value] of Object.entries(doc)) if (!(key in result)) result[key] = value;
  return result;
}

/** Set one top-level field, leaving the rest of the document as it is. */
export function withField(doc: PersonaDocument, key: string, value: unknown): PersonaDocument {
  return ordered({ ...doc, [key]: value });
}

/**
 * Rename a persona. Events that followed its old name (`old_name.done`) follow the new one,
 * so a new persona's events read as its own; events its author chose stay as they are.
 */
export function withName(doc: PersonaDocument, name: string): PersonaDocument {
  const before = `${eventPrefix(documentName(doc))}.`;
  const after = `${eventPrefix(name)}.`;
  const rename = (event: string) =>
    event.startsWith(before) ? `${after}${event.slice(before.length)}` : event;
  const produces = record(doc.produces);
  const outcomes = outcomesOf(doc);
  const renamed = outcomes.length
    ? withOutcomes(
        doc,
        outcomes.map((o) => ({ ...o, event: rename(o.event) })),
      )
    : doc;
  const eventType = text(produces.event_type);
  return withField(
    {
      ...renamed,
      produces: eventType
        ? { ...record(renamed.produces), event_type: rename(eventType) }
        : renamed.produces,
    },
    'name',
    name,
  );
}

export function withStartsOn(doc: PersonaDocument, events: string[]): PersonaDocument {
  return withField(doc, 'consumes', { ...record(doc.consumes), event_types: events });
}

/**
 * Set the outcomes: which events they send, the rule for each, and, where the persona
 * declares a verdict field in its outcome schema, the values that field may take.
 */
export function withOutcomes(doc: PersonaDocument, outcomes: PersonaOutcome[]): PersonaDocument {
  const {
    event_type: eventType,
    event_type_map: _map,
    when: _when,
    schema: rawSchema,
    ...rest
  } = record(doc.produces);
  // Written in this order so each outcome's rule sits right under the event it sends.
  const produces: Record<string, unknown> = {};
  if (eventType !== undefined) produces.event_type = eventType;
  produces.event_type_map = Object.fromEntries(outcomes.map((o) => [o.verdict, o.event]));
  const when = Object.fromEntries(
    outcomes.filter((o) => o.when.trim()).map((o) => [o.verdict, o.when.trim()]),
  );
  if (Object.keys(when).length > 0) produces.when = when;
  if (rawSchema !== undefined) {
    const schema = record(rawSchema);
    const verdict = record(schema.verdict);
    produces.schema =
      verdict.type === 'enum'
        ? { ...schema, verdict: { ...verdict, values: outcomes.map((o) => o.verdict) } }
        : rawSchema;
  }
  return withField(doc, 'produces', { ...produces, ...rest });
}

/** An outcome added by name; it sends `<persona>.<outcome>` until its author changes that. */
export function newOutcome(doc: PersonaDocument, verdict: string): PersonaOutcome {
  return { verdict, event: `${eventPrefix(documentName(doc))}.${verdict}`, when: '' };
}

/** Rename one outcome; an event that followed its old name (`persona.old`) follows the new. */
export function renameOutcome(
  doc: PersonaDocument,
  index: number,
  verdict: string,
): PersonaDocument {
  const prefix = eventPrefix(documentName(doc));
  const outcomes = outcomesOf(doc).map((outcome, i) => {
    if (i !== index) return outcome;
    const followed = outcome.event === `${prefix}.${outcome.verdict}`;
    return { ...outcome, verdict, event: followed ? `${prefix}.${verdict}` : outcome.event };
  });
  return withOutcomes(doc, outcomes);
}

export interface DocumentProblem {
  /** The wizard step that fixes it. */
  step: 'name' | 'starts' | 'finishes' | 'instructions';
  message: string;
}

/** What stops this document being saved, in the words of the step that fixes it. */
export function documentProblems(
  doc: PersonaDocument,
  options: { taken?: readonly string[] } = {},
): DocumentProblem[] {
  const problems: DocumentProblem[] = [];
  const name = documentName(doc).trim();
  if (!name) problems.push({ step: 'name', message: 'Give it a name.' });
  else if (!DOCUMENT_NAME_PATTERN.test(name)) {
    problems.push({
      step: 'name',
      message: 'Use letters, digits, dashes and underscores, starting with a letter or digit.',
    });
  } else if (options.taken?.includes(name)) {
    problems.push({ step: 'name', message: `A persona named ${name} already exists.` });
  }
  for (const event of startsOn(doc)) {
    if (!EVENT_PATTERN.test(event)) {
      problems.push({
        step: 'starts',
        message: `${event || 'An empty event'} is not an event name: lowercase words joined by dots.`,
      });
    }
  }
  const outcomes = outcomesOf(doc);
  if (outcomes.length === 0) {
    problems.push({ step: 'finishes', message: 'Add at least one way it can finish.' });
  }
  for (const outcome of outcomes) {
    if (!VERDICT_PATTERN.test(outcome.verdict)) {
      problems.push({
        step: 'finishes',
        message: `${outcome.verdict} is not an outcome name: lowercase words joined by underscores.`,
      });
    }
    if (!EVENT_PATTERN.test(outcome.event)) {
      problems.push({
        step: 'finishes',
        message: `${outcome.verdict} sends ${outcome.event || 'no event'}, which is not an event name.`,
      });
    }
    if (!outcome.when.trim()) {
      problems.push({ step: 'finishes', message: `Say when to report ${outcome.verdict}.` });
    }
  }
  const verdicts = new Set(outcomes.map((o) => o.verdict));
  for (const verdict of Object.keys(record(record(doc.produces).when))) {
    if (!verdicts.has(verdict)) {
      problems.push({
        step: 'finishes',
        message: `produces.when names ${verdict}, which is not one of its outcomes.`,
      });
    }
  }
  if (!documentInstructions(doc).trim()) {
    problems.push({ step: 'instructions', message: 'Tell it what to do.' });
  }
  return problems;
}

/** The document as YAML: exactly what is saved. */
export function documentYaml(doc: PersonaDocument): string {
  return dump(ordered(doc), { lineWidth: 100, noRefs: true });
}

export type ParsedYaml = { ok: true; doc: PersonaDocument } | { ok: false; error: string };

/** Read YAML typed by hand; a mistake is named with its line. */
export function parseDocumentYaml(source: string): ParsedYaml {
  let loaded: unknown;
  try {
    loaded = load(source);
  } catch (error) {
    const mark = (error as { mark?: { line?: number } }).mark;
    const reason = (error as { reason?: string }).reason ?? 'not valid YAML';
    return {
      ok: false,
      error: mark?.line !== undefined ? `Line ${mark.line + 1}: ${reason}` : reason,
    };
  }
  if (!loaded || typeof loaded !== 'object' || Array.isArray(loaded)) {
    return { ok: false, error: 'A persona is a mapping of its fields, starting with name.' };
  }
  return { ok: true, doc: loaded as PersonaDocument };
}

/** An event something already sends, and who sends it, so a newcomer can tell what it means. */
export interface KnownEvent {
  event: string;
  senders: string[];
}

/** Every event the given personas send, with which persona sends it on which outcome. */
export function knownEvents(personas: readonly PersonaSummary[]): KnownEvent[] {
  const senders = new Map<string, string[]>();
  for (const persona of personas) {
    for (const [verdict, event] of Object.entries(persona.outcomeEvents ?? {})) {
      const list = senders.get(event) ?? [];
      list.push(`${persona.name} on ${verdict}`);
      senders.set(event, list);
    }
  }
  return [...senders.entries()]
    .map(([event, list]) => ({ event, senders: list }))
    .sort((a, b) => a.event.localeCompare(b.event));
}
