import { useState, type CSSProperties } from 'react';
import { cn } from '../../../utils/cn';
import { verdictLabel, verdictTone, type WorkerOutcome } from '../../workers';
import { MarkdownContent } from '../MarkdownContent';
import { VerdictPill } from '../VerdictPill';
import { useOutcomeSpeaker, type OutcomeSpeaker } from './OutcomeSpeaker';
import './OutcomeCard.css';

const WRAPPED_OUTCOME_START = /---\s*o\s*u\s*t\s*c\s*o\s*m\s*e\s*---/i;
const WRAPPED_OUTCOME_END = /---\s*e\s*n\s*d\s*---/i;
const SOFT_KEY_FRAGMENT = /^[A-Za-z_][A-Za-z0-9_-]*$/;
const INLINE_LABEL_TEXT = /^[A-Z][A-Za-z0-9 /&()_-]{2,48}$/;
const INLINE_LABEL_CHAR = /[A-Za-z0-9 /&()_-]/;

function parseKeyValueLine(line: string): { key: string; value: string } | null {
  const colon = line.indexOf(':');
  if (colon <= 0) return null;

  const key = line.slice(0, colon).trim();
  if (!key || !SOFT_KEY_FRAGMENT.test(key)) return null;

  return {
    key,
    value: line.slice(colon + 1).trim(),
  };
}

function joinSoftWrappedParts(parts: string[], compact = false): string {
  const cleaned = parts.map((part) => part.trim()).filter(Boolean);
  if (compact) return cleaned.join('');

  let result = '';
  let previous = '';
  for (const part of cleaned) {
    if (!result) {
      result = part;
    } else if (/^[.,;:!?)]/.test(part) || result.endsWith('(') || result.endsWith('/')) {
      result += part;
    } else if (/^[.,;:!?)]$/.test(previous)) {
      result += ` ${part}`;
    } else if (part.startsWith('/') || part.startsWith('_') || part.startsWith('-')) {
      result += part;
    } else if (previous.length === 1 && /^[a-z]/.test(part)) {
      result += part;
    } else if (previous.startsWith('-') && /^[a-z]{1,3}$/.test(part)) {
      result += part;
    } else {
      result += ` ${part}`;
    }
    previous = part;
  }
  return result;
}

function mergeSoftWrappedKeyLine(lines: string[], startIndex: number): [string, number] {
  const stripped = (lines[startIndex] ?? '').trim();
  const inlineMatch = parseKeyValueLine(stripped);
  if (inlineMatch) return [stripped, startIndex + 1];
  if (!SOFT_KEY_FRAGMENT.test(stripped)) return [stripped, startIndex + 1];

  const fragments = [stripped];
  let index = startIndex + 1;
  while (index < lines.length) {
    const next = (lines[index] ?? '').trim();
    if (!next) break;

    const inline = parseKeyValueLine(next);
    if (inline) {
      const mergedKey = `${fragments.join('')}${inline.key}`;
      const mergedValue = inline.value;
      return [mergedValue ? `${mergedKey}: ${mergedValue}` : `${mergedKey}:`, index + 1];
    }

    if (next.startsWith(':')) {
      return [`${fragments.join('')}${next}`, index + 1];
    }

    if (!SOFT_KEY_FRAGMENT.test(next)) break;
    fragments.push(next);
    index += 1;
  }

  return [stripped, startIndex + 1];
}

function shouldNormalizeSoftWrappedOutcomeRaw(raw: string): boolean {
  const lines = raw.split(/\r?\n/).map((line) => line.trim());
  for (let index = 0; index < lines.length; index += 1) {
    const current = lines[index] ?? '';
    const next = lines[index + 1] ?? '';
    if (current === ':') return true;
    if (SOFT_KEY_FRAGMENT.test(current) && (SOFT_KEY_FRAGMENT.test(next) || next.startsWith(':'))) {
      return true;
    }
  }
  return false;
}

function normalizeSoftWrappedOutcomeRaw(raw: string): string {
  if (!shouldNormalizeSoftWrappedOutcomeRaw(raw)) return raw.trim();
  const lines = raw.split(/\r?\n/);
  const entries: Array<[string, string]> = [];
  let currentKey: string | null = null;
  let scalarParts: string[] = [];
  let index = 0;

  const flush = () => {
    if (!currentKey) return;
    const compactKey =
      currentKey === 'verdict' || currentKey === 'page_path' || currentKey === 'source_id';
    entries.push([currentKey, joinSoftWrappedParts(scalarParts, compactKey)]);
    currentKey = null;
    scalarParts = [];
  };

  while (index < lines.length) {
    const [merged, nextIndex] = mergeSoftWrappedKeyLine(lines, index);
    index = nextIndex;
    if (!merged) continue;

    const match = parseKeyValueLine(merged);
    if (match) {
      flush();
      const key = match.key;
      if (!key) continue;
      currentKey = key;
      const value = match.value;
      if (value) scalarParts.push(value);
      continue;
    }

    if (currentKey) {
      scalarParts.push(merged);
    }
  }

  flush();
  if (entries.length === 0) return raw.trim();
  return entries
    .map(([key, value]) => {
      if (!value.includes('\n')) return `${key}: ${value}`;
      const indented = value
        .split('\n')
        .map((line) => `  ${line}`)
        .join('\n');
      return `${key}: |\n${indented}`;
    })
    .join('\n');
}

function normalizeOutcomeMarkers(text: string): string {
  return text
    .replace(WRAPPED_OUTCOME_START, '---outcome---')
    .replace(WRAPPED_OUTCOME_END, '---end---');
}

/** An outcome written as a JSON object, as a coordinator writes its working state. */
function parseJsonObject(raw: string): Record<string, unknown> | null {
  const trimmed = raw.trim();
  if (!trimmed.startsWith('{')) return null;
  try {
    const value: unknown = JSON.parse(trimmed);
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    // Not JSON: the outcome is the usual key: value lines.
    return null;
  }
}

/** A JSON outcome's fields; nested objects are read through and lists become bullets. */
function jsonFields(value: Record<string, unknown>): Record<string, string> {
  const fields: Record<string, string> = {};
  const add = (key: string, entry: unknown) => {
    if (entry == null || entry === '') return;
    if (Array.isArray(entry)) {
      const items = entry.filter((item) => item != null && item !== '');
      if (items.length === 0) return;
      fields[key] = items
        .map((item) => `- ${typeof item === 'string' ? item : JSON.stringify(item)}`)
        .join('\n');
      return;
    }
    if (typeof entry === 'object') {
      for (const [inner, innerValue] of Object.entries(entry)) add(inner, innerValue);
      return;
    }
    fields[key] = String(entry);
  };
  for (const [key, entry] of Object.entries(value)) add(key, entry);
  return fields;
}

/** An outcome block's fields, from JSON or `key: value` lines. */
export function parseOutcomeFields(raw: string): Record<string, string> {
  const json = parseJsonObject(raw);
  if (json) return jsonFields(json);
  const normalizedRaw = normalizeSoftWrappedOutcomeRaw(raw);
  const fields: Record<string, string> = {};
  const lines = normalizedRaw.split('\n');

  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? '';
    const colon = line.indexOf(':');
    if (colon === -1) {
      i++;
      continue;
    }

    const key = line.slice(0, colon).trim();
    const value = line.slice(colon + 1).trim();
    if (!key) {
      i++;
      continue;
    }

    if (value === '|' || value === '>') {
      const preserveNewlines = value === '|';
      const blockLines: string[] = [];
      let blockIndent: number | null = null;
      i++;

      while (i < lines.length) {
        const nextLine = lines[i] ?? '';
        const trimmed = nextLine.trim();

        if (trimmed.length === 0) {
          blockLines.push('');
          i++;
          continue;
        }

        const indent = nextLine.length - nextLine.trimStart().length;
        if (blockIndent == null) {
          blockIndent = indent;
        }

        if (indent < blockIndent) {
          break;
        }

        blockLines.push(nextLine.slice(blockIndent));
        i++;
      }

      fields[key] = preserveNewlines
        ? blockLines.join('\n').trim()
        : blockLines.join(' ').replace(/\s+/g, ' ').trim();
      continue;
    }

    fields[key] = value;
    i++;
  }
  return fields;
}

function restoreInlineLabelBullets(value: string): string {
  let result = '';
  let cursor = 0;

  while (cursor < value.length) {
    const marker = value.indexOf(': - ', cursor);
    if (marker === -1) {
      result += value.slice(cursor);
      break;
    }

    let labelStart = marker - 1;
    while (labelStart >= cursor && INLINE_LABEL_CHAR.test(value[labelStart] ?? '')) {
      labelStart -= 1;
    }
    labelStart += 1;

    const label = value.slice(labelStart, marker).trimStart();
    if (!INLINE_LABEL_TEXT.test(label)) {
      result += value.slice(cursor, marker + 4);
      cursor = marker + 4;
      continue;
    }

    result += value.slice(cursor, labelStart).trimEnd();
    const spacer = result.trim() ? '\n\n' : '';
    result += `${spacer}${label}:\n- `;
    cursor = marker + 4;
  }

  return result;
}

function headingMarkerLength(value: string, index: number): number {
  let cursor = index;
  while (cursor < value.length && value[cursor] === '#' && cursor - index < 6) {
    cursor += 1;
  }
  const markerLength = cursor - index;
  if (markerLength < 1 || markerLength > 6) return 0;
  return value[cursor] === ' ' ? markerLength : 0;
}

function restoreInlineHeadingBreaks(value: string): string {
  let result = '';
  let cursor = 0;

  while (cursor < value.length) {
    const char = value[cursor] ?? '';
    if (char.trim() === '' && headingMarkerLength(value, cursor + 1) > 0) {
      result = result.trimEnd();
      result += '\n\n';
      cursor += 1;
      while (cursor < value.length && (value[cursor] ?? '').trim() === '') {
        cursor += 1;
      }
      continue;
    }
    result += char;
    cursor += 1;
  }

  return result;
}

function isInlineBulletStart(value: string): boolean {
  return /^[A-Z0-9`*]$/.test(value);
}

function restoreInlineBullets(value: string): string {
  let result = '';
  let cursor = 0;

  while (cursor < value.length) {
    const marker = value.indexOf(' - ', cursor);
    if (marker === -1) {
      result += value.slice(cursor);
      break;
    }

    const nextChar = value[marker + 3] ?? '';
    if (!isInlineBulletStart(nextChar)) {
      result += value.slice(cursor, marker + 3);
      cursor = marker + 3;
      continue;
    }

    result += value.slice(cursor, marker);
    result += '\n- ';
    cursor = marker + 3;
  }

  return result;
}

function restoreHeadingBulletBreaks(value: string): string {
  const lines = value.split('\n');
  return lines
    .map((line) => {
      const markerLength = headingMarkerLength(line, 0);
      if (markerLength < 2) return line;

      const bulletIndex = line.indexOf('- ');
      if (bulletIndex <= markerLength + 1) return line;
      return `${line.slice(0, bulletIndex)}\n${line.slice(bulletIndex)}`;
    })
    .join('\n');
}

function restoreInlineMarkdownBreaks(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed.includes('\n')) return trimmed;

  return restoreHeadingBulletBreaks(
    restoreInlineBullets(restoreInlineLabelBullets(restoreInlineHeadingBreaks(trimmed))),
  ).trim();
}

/** Fields the card shows in its own slots rather than as facts. */
const SLOT_FIELDS = new Set(['verdict', 'status', 'summary', 'result', 'event_type', 'eventType']);

/**
 * A field that is one short plain line reads as a fact chip next to the others; a longer
 * one, a multi-line one or one written in markdown keeps its own block below, rendered in
 * full. This sorts fields, it never drops one.
 */
const FACT_MAX_LENGTH = 48;
const MARKDOWN_SYNTAX = /[`*[\]#]|^\s*(?:[-+>]|\d+[.)])\s/;

function isFact(value: string): boolean {
  return value.length <= FACT_MAX_LENGTH && !value.includes('\n') && !MARKDOWN_SYNTAX.test(value);
}

interface OutcomeCardProps {
  raw: string;
  /** The worker who reported it; defaults to the one speaking the message that carries it. */
  speaker?: OutcomeSpeaker;
}

interface OutcomeViewProps {
  raw: string;
  fields: Readonly<Record<string, string>>;
  speaker: OutcomeSpeaker | null;
}

function OutcomeView({ raw, fields, speaker }: OutcomeViewProps) {
  const [showRaw, setShowRaw] = useState(false);
  const verdict = fields.verdict ?? fields.status ?? '';
  const summary = fields.summary ?? fields.result ?? '';
  const eventType = fields.event_type ?? fields.eventType ?? '';
  const tone = verdictTone(verdict || undefined);
  const rest = Object.entries(fields).filter(([key]) => !SLOT_FIELDS.has(key));
  const facts = rest.filter(([, value]) => isFact(value));
  const blocks = rest.filter(([, value]) => !isFact(value));

  return (
    <div
      className={cn(
        'niuu-chat-outcome',
        `niuu-chat-outcome--tone-${tone}`,
        verdict && `niuu-chat-outcome--${verdict}`,
      )}
      style={speaker ? ({ '--niuu-worker-color': speaker.color } as CSSProperties) : undefined}
      data-testid="outcome-card"
    >
      <div className="niuu-chat-outcome-header">
        <span className="niuu-chat-outcome-label">Outcome</span>
        {speaker && <b className="niuu-chat-outcome-worker">{speaker.name}</b>}
        {verdict && <VerdictPill label={verdictLabel(verdict)} tone={tone} />}
        {eventType && <span className="niuu-chat-outcome-event">{eventType}</span>}
      </div>
      {summary && (
        <div className="niuu-chat-outcome-summary">
          <MarkdownContent content={restoreInlineMarkdownBreaks(summary)} />
        </div>
      )}
      {facts.length > 0 && (
        <div className="niuu-chat-outcome-facts">
          {facts.map(([key, value]) => (
            <span key={key} className="niuu-chat-outcome-fact">
              <b>{key.replace(/_/g, ' ')}</b>
              {value}
            </span>
          ))}
        </div>
      )}
      {blocks.length > 0 && (
        <div className="niuu-chat-outcome-fields">
          {blocks.map(([key, value]) => (
            <div key={key} className="niuu-chat-outcome-field">
              <span className="niuu-chat-outcome-field-key">{key.replace(/_/g, ' ')}</span>
              <div className="niuu-chat-outcome-field-value">
                <MarkdownContent content={restoreInlineMarkdownBreaks(value)} />
              </div>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        className="niuu-chat-outcome-toggle"
        onClick={() => setShowRaw((prev) => !prev)}
      >
        {showRaw ? 'Hide raw' : 'Show raw'}
      </button>
      {showRaw && <pre className="niuu-chat-outcome-raw">{raw}</pre>}
    </div>
  );
}

export function OutcomeCard({ raw, speaker }: OutcomeCardProps) {
  const fromMessage = useOutcomeSpeaker();
  return (
    <OutcomeView raw={raw} fields={parseOutcomeFields(raw)} speaker={speaker ?? fromMessage} />
  );
}

function fieldText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

/**
 * The same card for an outcome that arrived only as a live event, with no transcript message
 * to carry it. Its fields are the event's own; nothing is invented to fill the card.
 */
export function WorkerOutcomeCard({
  outcome,
  speaker,
}: {
  outcome: WorkerOutcome;
  speaker: OutcomeSpeaker;
}) {
  const fields: Record<string, string> = {};
  for (const [key, value] of Object.entries(outcome.fields)) {
    const text = fieldText(value);
    if (text) fields[key] = text;
  }
  if (outcome.verdict) fields.verdict = outcome.verdict;
  if (outcome.summary) fields.summary = outcome.summary;
  fields.event_type = outcome.eventType;
  const raw = Object.entries(fields)
    .map(([key, value]) => `${key}: ${value}`)
    .join('\n');
  return <OutcomeView raw={raw} fields={fields} speaker={speaker} />;
}

/**
 * Detect if text contains an outcome block and extract the raw content.
 */
export function extractOutcomeBlock(
  text: string,
): { before: string; raw: string; after: string } | null {
  const normalizedText = normalizeOutcomeMarkers(text);
  const fenced = extractFencedOutcomeBlock(normalizedText);
  const tagged = extractTaggedOutcomeBlock(normalizedText);
  const dashed = extractDashedOutcomeBlock(normalizedText);

  const candidates = [fenced, tagged, dashed].filter(
    (candidate): candidate is { before: string; raw: string; after: string } => candidate !== null,
  );

  if (candidates.length === 0) return null;
  return candidates.reduce((best, candidate) =>
    candidate.before.length <= best.before.length ? candidate : best,
  );
}

function extractFencedOutcomeBlock(
  text: string,
): { before: string; raw: string; after: string } | null {
  const marker = '```outcome';
  const start = text.indexOf(marker);
  if (start === -1) return null;

  let contentStart = start + marker.length;
  if (text[contentStart] === '\n') {
    contentStart += 1;
  }

  const end = text.indexOf('```', contentStart);
  if (end === -1) return null;

  return {
    before: text.slice(0, start),
    raw: normalizeSoftWrappedOutcomeRaw(text.slice(contentStart, end).trim()),
    after: text.slice(end + 3),
  };
}

function extractTaggedOutcomeBlock(
  text: string,
): { before: string; raw: string; after: string } | null {
  const openTag = '<outcome>';
  const closeTag = '</outcome>';
  const start = text.indexOf(openTag);
  if (start === -1) return null;

  const contentStart = start + openTag.length;
  const end = text.indexOf(closeTag, contentStart);
  if (end === -1) return null;

  return {
    before: text.slice(0, start),
    raw: normalizeSoftWrappedOutcomeRaw(text.slice(contentStart, end).trim()),
    after: text.slice(end + closeTag.length),
  };
}

function extractDashedOutcomeBlock(
  text: string,
): { before: string; raw: string; after: string } | null {
  const lower = text.toLowerCase();
  const openMarker = '---outcome---';
  const start = lower.indexOf(openMarker);
  if (start === -1) return null;

  let contentStart = start + openMarker.length;
  while (contentStart < text.length && /\s/.test(text[contentStart] ?? '')) {
    contentStart += 1;
  }

  // A JSON outcome ends where its object closes, so dashes inside its strings and prose
  // after it stay where they belong. A closing marker after the object is optional.
  const jsonEnd = text[contentStart] === '{' ? jsonObjectEnd(text, contentStart) : null;
  const json = jsonEnd == null ? null : text.slice(contentStart, jsonEnd);
  if (jsonEnd != null && json && parseJsonObject(json)) {
    const closer = /^\s*---(?:end---)?(?=\s|$)/.exec(text.slice(jsonEnd));
    return {
      before: text.slice(0, start),
      raw: json,
      after: text.slice(jsonEnd + (closer?.[0].length ?? 0)),
    };
  }

  const endMarker = findDashedOutcomeEnd(lower, contentStart);
  if (endMarker == null) return null;

  const raw = normalizeSoftWrappedOutcomeRaw(text.slice(contentStart, endMarker.start).trim());

  return {
    before: text.slice(0, start),
    raw,
    after: text.slice(endMarker.start + endMarker.marker.length),
  };
}

/** Index just past the object opening at `start`; braces inside strings do not count. */
function jsonObjectEnd(text: string, start: number): number | null {
  let depth = 0;
  let inString = false;
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      if (char === '\\') index += 1;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === '{') depth += 1;
    else if (char === '}') {
      depth -= 1;
      if (depth === 0) return index + 1;
    }
  }
  return null;
}

function findDashedOutcomeEnd(
  lower: string,
  fromIndex: number,
): { start: number; marker: '---end---' | '---' } | null {
  const endTag = '---end---';
  const closeTag = '---';

  let best: { start: number; marker: '---end---' | '---' } | null = null;

  const endTagIndex = lower.indexOf(endTag, fromIndex);
  if (endTagIndex !== -1) {
    const nextChar = lower[endTagIndex + endTag.length];
    if (nextChar == null || /\s/.test(nextChar)) {
      best = { start: endTagIndex, marker: '---end---' };
    }
  }

  let closeIndex = lower.indexOf(closeTag, fromIndex);
  while (closeIndex !== -1) {
    const nextChar = lower[closeIndex + closeTag.length];
    if (nextChar == null || /\s/.test(nextChar)) {
      if (best == null || closeIndex < best.start) {
        best = { start: closeIndex, marker: '---' };
      }
      break;
    }
    closeIndex = lower.indexOf(closeTag, closeIndex + closeTag.length);
  }

  return best;
}
