import { useState } from 'react';
import { Copy, Check, ChevronRight, ChevronDown, WrapText } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { OutcomeCard, extractOutcomeBlock } from '../OutcomeCard';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';
import './MarkdownContent.css';

const CURSOR_CHAR = '▊';

interface CodeBlockProps {
  language?: string;
  code: string;
}

interface SessionSummaryPayload {
  summary: string;
  key_changes?: string[];
  unfinished_work?: string | string[] | null;
}

function CodeBlock({ language, code }: CodeBlockProps) {
  const [copied, handleCopy] = useCopyFeedback(code);
  const [collapsed, setCollapsed] = useState(false);
  const [wordWrap, setWordWrap] = useState(false);

  return (
    <div className="niuu-chat-md-codeblock" data-testid="code-block">
      <div className="niuu-chat-md-codeblock-header">
        {language && <span className="niuu-chat-md-codeblock-lang">{language}</span>}
        <div className="niuu-chat-md-codeblock-actions">
          <button
            type="button"
            className="niuu-chat-md-codeblock-btn"
            onClick={() => setWordWrap((prev) => !prev)}
            title={wordWrap ? 'Disable word wrap' : 'Enable word wrap'}
            aria-pressed={wordWrap}
          >
            <WrapText className="niuu-chat-md-codeblock-btn-icon" />
          </button>
          <button
            type="button"
            className="niuu-chat-md-codeblock-btn"
            onClick={() => setCollapsed((prev) => !prev)}
            title={collapsed ? 'Expand' : 'Collapse'}
          >
            {collapsed ? (
              <ChevronRight className="niuu-chat-md-codeblock-btn-icon" />
            ) : (
              <ChevronDown className="niuu-chat-md-codeblock-btn-icon" />
            )}
          </button>
          <button
            type="button"
            className="niuu-chat-md-codeblock-btn"
            onClick={handleCopy}
            title={copied ? 'Copied!' : 'Copy'}
          >
            {copied ? (
              <Check className="niuu-chat-md-codeblock-btn-icon" />
            ) : (
              <Copy className="niuu-chat-md-codeblock-btn-icon" />
            )}
          </button>
        </div>
      </div>
      {!collapsed && (
        <pre
          className={cn(
            'niuu-chat-md-codeblock-pre',
            wordWrap && 'niuu-chat-md-codeblock-pre--wrap',
          )}
        >
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

/**
 * Parse text into segments: plain text and fenced code blocks.
 */
type Segment =
  | { type: 'text'; content: string }
  | { type: 'code'; language: string; content: string }
  | { type: 'outcome'; raw: string }
  | { type: 'session_summary'; payload: SessionSummaryPayload };

function SessionSummaryCard({ payload }: { payload: SessionSummaryPayload }) {
  const unfinishedItems = Array.isArray(payload.unfinished_work)
    ? payload.unfinished_work.filter(Boolean)
    : payload.unfinished_work
      ? [payload.unfinished_work]
      : [];

  return (
    <section className="niuu-chat-md-summary-card" data-testid="session-summary-card">
      <div className="niuu-chat-md-summary-card-eyebrow">Session summary</div>
      <p className="niuu-chat-md-summary-card-text">{renderInline(payload.summary)}</p>
      {payload.key_changes && payload.key_changes.length > 0 && (
        <div className="niuu-chat-md-summary-card-section">
          <h4 className="niuu-chat-md-summary-card-heading">Key changes</h4>
          <ul className="niuu-chat-md-summary-card-list">
            {payload.key_changes.map((item, index) => (
              <li key={index}>{renderInline(item)}</li>
            ))}
          </ul>
        </div>
      )}
      {unfinishedItems.length > 0 && (
        <div className="niuu-chat-md-summary-card-section">
          <h4 className="niuu-chat-md-summary-card-heading">Unfinished work</h4>
          {unfinishedItems.length === 1 ? (
            <p className="niuu-chat-md-summary-card-text">
              {renderInline(unfinishedItems[0] ?? '')}
            </p>
          ) : (
            <ul className="niuu-chat-md-summary-card-list">
              {unfinishedItems.map((item, index) => (
                <li key={index}>{renderInline(item)}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function parseSegments(text: string): Segment[] {
  const segments: Segment[] = [];
  let cursor = 0;

  while (cursor < text.length) {
    const fenceStart = text.indexOf('```', cursor);
    if (fenceStart === -1) break;

    if (fenceStart > cursor) {
      pushTextLikeSegment(segments, text.slice(cursor, fenceStart));
    }

    const languageStart = fenceStart + 3;
    const newlineIndex = text.indexOf('\n', languageStart);
    if (newlineIndex === -1) break;

    const fenceEnd = text.indexOf('```', newlineIndex + 1);
    if (fenceEnd === -1) break;

    const lang = text.slice(languageStart, newlineIndex).trim();
    const code = text.slice(newlineIndex + 1, fenceEnd);
    if (lang === 'outcome') {
      segments.push({ type: 'outcome', raw: code.trim() });
    } else {
      segments.push({ type: 'code', language: lang, content: code });
    }
    cursor = fenceEnd + 3;
  }

  if (cursor < text.length) {
    pushTextLikeSegment(segments, text.slice(cursor));
  }

  return segments;
}

function pushTextLikeSegment(segments: Segment[], textChunk: string) {
  const outcome = extractOutcomeBlock(textChunk);
  if (outcome) {
    if (outcome.before.trim()) {
      pushTextLikeSegment(segments, outcome.before);
    }
    segments.push({ type: 'outcome', raw: outcome.raw });
    if (outcome.after.trim()) {
      pushTextLikeSegment(segments, outcome.after);
    }
    return;
  }

  const summary = parseSessionSummary(textChunk);
  if (summary) {
    segments.push({ type: 'session_summary', payload: summary });
    return;
  }

  segments.push({ type: 'text', content: textChunk });
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

/**
 * Render a text segment with basic markdown-like formatting.
 * Handles: headings, bold, inline code, lists, blockquotes, links.
 */
function TextSegment({ content, isStreaming }: { content: string; isStreaming?: boolean }) {
  const lines = content.split('\n');
  const elements: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (line === undefined) {
      i++;
      continue;
    }

    // Heading
    const headingMatch = parseHeading(line);
    if (headingMatch) {
      const level = headingMatch.level;
      const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      elements.push(
        <Tag key={i} className={`niuu-chat-md-h${level}`}>
          {headingMatch.text}
        </Tag>,
      );
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith('> ')) {
      elements.push(
        <blockquote key={i} className="niuu-chat-md-blockquote">
          {line.slice(2)}
        </blockquote>,
      );
      i++;
      continue;
    }

    // Markdown table
    const headerCells = parseTableRow(line);
    const dividerLine = lines[i + 1];
    if (headerCells && dividerLine && isTableDivider(dividerLine)) {
      const rows: string[][] = [];
      let rowIndex = i + 2;
      while (rowIndex < lines.length) {
        const rowCells = parseTableRow(lines[rowIndex] ?? '');
        if (!rowCells || isTableDivider(lines[rowIndex] ?? '')) break;
        rows.push(rowCells);
        rowIndex += 1;
      }
      elements.push(
        <div key={`table-${i}`} className="niuu-chat-md-table-wrap">
          <table className="niuu-chat-md-table">
            <thead>
              <tr>
                {headerCells.map((cell, idx) => (
                  <th key={idx} className="niuu-chat-md-table-head">
                    {renderInline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIdx) => (
                <tr key={rowIdx}>
                  {row.map((cell, cellIdx) => (
                    <td key={cellIdx} className="niuu-chat-md-table-cell">
                      {renderInline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      i = rowIndex;
      continue;
    }

    // Unordered list item
    const unorderedItem = parseUnorderedListItem(line);
    if (unorderedItem !== null) {
      const listItems: string[] = [];
      while (i < lines.length) {
        const ln = lines[i];
        if (!ln) break;
        const item = parseUnorderedListItem(ln);
        if (item === null) break;
        listItems.push(item);
        i++;
      }
      elements.push(
        <ul key={`ul-${i}`} className="niuu-chat-md-ul">
          {listItems.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Ordered list item
    const orderedItem = parseOrderedListItem(line);
    if (orderedItem !== null) {
      const listItems: string[] = [];
      while (i < lines.length) {
        const ln = lines[i];
        if (!ln) break;
        const item = parseOrderedListItem(ln);
        if (item === null) break;
        listItems.push(item);
        i++;
      }
      elements.push(
        <ol key={`ol-${i}`} className="niuu-chat-md-ol">
          {listItems.map((item, idx) => (
            <li key={idx}>{renderInline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // Empty line
    if (!line.trim()) {
      i++;
      continue;
    }

    // Regular paragraph
    elements.push(
      <p key={i} className="niuu-chat-md-p">
        {renderInline(line)}
        {isStreaming && i === lines.length - 1 && (
          <span className="niuu-chat-md-cursor">{CURSOR_CHAR}</span>
        )}
      </p>,
    );
    i++;
  }

  return <>{elements}</>;
}

function renderInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  while (cursor < text.length) {
    if (text.startsWith('**', cursor)) {
      const end = text.indexOf('**', cursor + 2);
      if (end !== -1) {
        parts.push(<strong key={key++}>{text.slice(cursor + 2, end)}</strong>);
        cursor = end + 2;
        continue;
      }
    }

    if (text[cursor] === '`') {
      const end = text.indexOf('`', cursor + 1);
      if (end !== -1) {
        const code = text.slice(cursor + 1, end);
        parts.push(
          <code key={key++} className="niuu-chat-md-inline-code">
            {code}
          </code>,
        );
        cursor = end + 1;
        continue;
      }
    }

    if (text[cursor] === '[') {
      const labelEnd = text.indexOf('](', cursor + 1);
      if (labelEnd !== -1) {
        const urlEnd = text.indexOf(')', labelEnd + 2);
        if (urlEnd !== -1) {
          const label = text.slice(cursor + 1, labelEnd);
          const href = text.slice(labelEnd + 2, urlEnd);
          parts.push(
            <a
              key={key++}
              href={href}
              className="niuu-chat-md-link"
              target="_blank"
              rel="noreferrer"
            >
              {label}
            </a>,
          );
          cursor = urlEnd + 1;
          continue;
        }
      }
    }

    const next = findNextInlineToken(text, cursor);
    if (next === cursor) {
      parts.push(text[cursor]);
      cursor += 1;
      continue;
    }
    parts.push(text.slice(cursor, next));
    cursor = next;
  }

  return parts.length === 1 ? parts[0] : parts;
}

function parseHeading(line: string): { level: 1 | 2 | 3 | 4 | 5 | 6; text: string } | null {
  let level = 0;
  while (level < line.length && line[level] === '#') {
    level += 1;
  }

  if (level < 1 || level > 6) return null;
  if (line[level] !== ' ') return null;

  const text = line.slice(level + 1);
  if (!text) return null;

  return { level: level as 1 | 2 | 3 | 4 | 5 | 6, text };
}

function parseUnorderedListItem(line: string): string | null {
  if (line.length < 2) return null;
  if (!['-', '*', '+'].includes(line[0] ?? '')) return null;
  if (line[1] !== ' ') return null;
  return line.slice(2);
}

function parseOrderedListItem(line: string): string | null {
  let index = 0;
  while (index < line.length && isDigit(line[index] ?? '')) {
    index += 1;
  }

  if (index === 0) return null;
  if (line[index] !== '.' || line[index + 1] !== ' ') return null;
  return line.slice(index + 2);
}

function parseTableRow(line: string): string[] | null {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return null;
  const withoutLeading = trimmed.startsWith('|') ? trimmed.slice(1) : trimmed;
  const normalized = withoutLeading.endsWith('|') ? withoutLeading.slice(0, -1) : withoutLeading;
  const cells = normalized.split('|').map((cell) => cell.trim());
  if (cells.length < 2 || cells.some((cell) => cell.length === 0)) return null;
  return cells;
}

function isTableDivider(line: string): boolean {
  const cells = parseTableRow(line);
  if (!cells) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isDigit(char: string): boolean {
  return char >= '0' && char <= '9';
}

function findNextInlineToken(text: string, startAt: number): number {
  const candidates = [
    text.indexOf('**', startAt),
    text.indexOf('`', startAt),
    text.indexOf('[', startAt),
  ].filter((index) => index !== -1);

  if (candidates.length === 0) {
    return text.length;
  }

  return Math.min(...candidates);
}

interface MarkdownContentProps {
  content: string;
  isStreaming?: boolean;
}

export function MarkdownContent({ content, isStreaming = false }: MarkdownContentProps) {
  const segments = parseSegments(content);

  return (
    <div className="niuu-chat-md" data-testid="markdown-content">
      {segments.map((seg, i) => {
        if (seg.type === 'code') {
          return <CodeBlock key={i} language={seg.language} code={seg.content} />;
        }
        if (seg.type === 'outcome') {
          return <OutcomeCard key={i} raw={seg.raw} />;
        }
        if (seg.type === 'session_summary') {
          return <SessionSummaryCard key={i} payload={seg.payload} />;
        }
        return (
          <TextSegment
            key={i}
            content={seg.content}
            isStreaming={isStreaming && i === segments.length - 1}
          />
        );
      })}
    </div>
  );
}
