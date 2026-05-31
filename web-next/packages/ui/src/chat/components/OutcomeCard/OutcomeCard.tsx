import { useState } from 'react';
import { CheckCircle, XCircle, AlertTriangle, RotateCcw, ArrowUpCircle } from 'lucide-react';
import { cn } from '../../../utils/cn';
import type { MeshVerdict } from '../../types';
import { MarkdownContent } from '../MarkdownContent';
import './OutcomeCard.css';

const VERDICT_ICONS: Record<MeshVerdict, typeof CheckCircle> = {
  approve: CheckCircle,
  pass: CheckCircle,
  retry: RotateCcw,
  escalate: ArrowUpCircle,
  fail: XCircle,
  needs_changes: AlertTriangle,
  needs_review: AlertTriangle,
};

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

function parseFields(raw: string): Record<string, string> {
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

interface OutcomeCardProps {
  raw: string;
}

export function OutcomeCard({ raw }: OutcomeCardProps) {
  const [showRaw, setShowRaw] = useState(false);
  const fields = parseFields(raw);
  const verdict = (fields.verdict ?? fields.status ?? '') as MeshVerdict | '';
  const summary = fields.summary ?? fields.result ?? '';
  const Icon = verdict && VERDICT_ICONS[verdict] ? VERDICT_ICONS[verdict] : CheckCircle;

  return (
    <div
      className={cn('niuu-chat-outcome', verdict && `niuu-chat-outcome--${verdict}`)}
      data-testid="outcome-card"
    >
      <div className="niuu-chat-outcome-header">
        <Icon className="niuu-chat-outcome-icon" />
        <span className="niuu-chat-outcome-label">Outcome</span>
        {verdict && (
          <span className={`niuu-chat-outcome-badge niuu-chat-outcome-badge--${verdict}`}>
            {verdict}
          </span>
        )}
      </div>
      {summary && (
        <div className="niuu-chat-outcome-summary">
          <MarkdownContent content={restoreInlineMarkdownBreaks(summary)} />
        </div>
      )}
      <div className="niuu-chat-outcome-fields">
        {Object.entries(fields)
          .filter(([k]) => k !== 'verdict' && k !== 'status' && k !== 'summary' && k !== 'result')
          .map(([k, v]) => (
            <div key={k} className="niuu-chat-outcome-field">
              <span className="niuu-chat-outcome-field-key">{k}</span>
              <div className="niuu-chat-outcome-field-value">
                <MarkdownContent content={restoreInlineMarkdownBreaks(v)} />
              </div>
            </div>
          ))}
      </div>
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

  const endMarker = findDashedOutcomeEnd(lower, contentStart);
  if (endMarker == null) return null;

  const raw = normalizeSoftWrappedOutcomeRaw(text.slice(contentStart, endMarker.start).trim());

  return {
    before: text.slice(0, start),
    raw,
    after: text.slice(endMarker.start + endMarker.marker.length),
  };
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
