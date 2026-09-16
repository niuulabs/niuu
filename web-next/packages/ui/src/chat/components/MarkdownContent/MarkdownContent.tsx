import { LexiMarkdown } from './LexiMarkdown';
import { OutcomeCard, extractOutcomeBlock } from '../OutcomeCard';
import './MarkdownContent.css';

interface SessionSummaryPayload {
  summary: string;
  key_changes?: string[];
  unfinished_work?: string | string[] | null;
}

function SessionSummaryCard({ payload }: { payload: SessionSummaryPayload }) {
  const unfinishedItems = Array.isArray(payload.unfinished_work)
    ? payload.unfinished_work.filter(Boolean)
    : payload.unfinished_work
      ? [payload.unfinished_work]
      : [];

  return (
    <section className="niuu-chat-md-summary-card" data-testid="session-summary-card">
      <div className="niuu-chat-md-summary-card-eyebrow">Session summary</div>
      <p className="niuu-chat-md-summary-card-text">{renderSummaryInline(payload.summary)}</p>
      {payload.key_changes && payload.key_changes.length > 0 && (
        <div className="niuu-chat-md-summary-card-section">
          <h4 className="niuu-chat-md-summary-card-heading">Key changes</h4>
          <ul className="niuu-chat-md-summary-card-list">
            {payload.key_changes.map((item, index) => (
              <li key={index}>{renderSummaryInline(item)}</li>
            ))}
          </ul>
        </div>
      )}
      {unfinishedItems.length > 0 && (
        <div className="niuu-chat-md-summary-card-section">
          <h4 className="niuu-chat-md-summary-card-heading">Unfinished work</h4>
          {unfinishedItems.length === 1 ? (
            <p className="niuu-chat-md-summary-card-text">
              {renderSummaryInline(unfinishedItems[0] ?? '')}
            </p>
          ) : (
            <ul className="niuu-chat-md-summary-card-list">
              {unfinishedItems.map((item, index) => (
                <li key={index}>{renderSummaryInline(item)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function parseSessionSummary(content: string): SessionSummaryPayload | null {
  const trimmed = content.trim();
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed);
    if (!isSessionSummaryPayload(parsed)) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function isSessionSummaryPayload(value: unknown): value is SessionSummaryPayload {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return false;
  }

  const record = value as Record<string, unknown>;
  const allowedKeys = new Set(['summary', 'key_changes', 'unfinished_work']);
  if (Object.keys(record).some((key) => !allowedKeys.has(key))) {
    return false;
  }

  if (typeof record.summary !== 'string' || record.summary.trim().length === 0) {
    return false;
  }

  if (
    record.key_changes !== undefined &&
    (!Array.isArray(record.key_changes) ||
      record.key_changes.some((item) => typeof item !== 'string' || item.trim().length === 0))
  ) {
    return false;
  }

  if (
    record.unfinished_work !== undefined &&
    record.unfinished_work !== null &&
    typeof record.unfinished_work !== 'string' &&
    (!Array.isArray(record.unfinished_work) ||
      record.unfinished_work.some((item) => typeof item !== 'string' || item.trim().length === 0))
  ) {
    return false;
  }

  return true;
}

function renderSummaryInline(text: string) {
  return <LexiMarkdown content={text} inline />;
}

interface MarkdownContentProps {
  content: string;
  isStreaming?: boolean;
}

/** Shared by live Claude/Codex messages, settled transcripts and document previews. */
export function MarkdownContent({ content, isStreaming = false }: MarkdownContentProps) {
  const summary = parseSessionSummary(content);
  return (
    <div className="niuu-chat-md" data-testid="markdown-content">
      {summary ? (
        <SessionSummaryCard payload={summary} />
      ) : (
        <LegacyOutcomeContent content={content} isStreaming={isStreaming} />
      )}
      {isStreaming && (
        <span className="niuu-chat-md-cursor" aria-hidden="true">
          ▊
        </span>
      )}
    </div>
  );
}

function LegacyOutcomeContent({ content, isStreaming }: MarkdownContentProps) {
  // Search prose only, so examples of outcome markers inside code stay literal.
  const fence = /^ {0,3}(`{3,}|~{3,})[^\n]*\n/gm;
  let cursor = 0;
  let match: RegExpExecArray | null;
  const prose: { start: number; text: string }[] = [];
  while ((match = fence.exec(content))) {
    prose.push({ start: cursor, text: content.slice(cursor, match.index) });
    const marker = match[1]!;
    const closer = new RegExp(`^ {0,3}${marker[0]}{${marker.length},}[ \t]*(?:\\n|$)`, 'gm');
    closer.lastIndex = fence.lastIndex;
    cursor = closer.exec(content) ? closer.lastIndex : content.length;
    fence.lastIndex = cursor;
  }
  prose.push({ start: cursor, text: content.slice(cursor) });
  for (const part of prose) {
    const outcome = extractOutcomeBlock(part.text);
    if (outcome) {
      const start = part.start + outcome.before.length;
      const end = part.start + part.text.length - outcome.after.length;
      return (
        <>
          <LexiMarkdown content={content.slice(0, start)} isStreaming={isStreaming} />
          <OutcomeCard raw={outcome.raw} />
          <LegacyOutcomeContent content={content.slice(end)} isStreaming={isStreaming} />
        </>
      );
    }
  }
  return <LexiMarkdown content={content} isStreaming={isStreaming} />;
}
