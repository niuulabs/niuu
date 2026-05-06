import { useCallback, useState, type ReactNode } from 'react';
import { cn } from '@/utils';
import { OutcomeCard, OUTCOME_RE, OUTCOME_EXTRACT_RE } from './OutcomeCard';
import styles from './RenderedContent.module.css';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface RenderedContentProps {
  content: string;
  className?: string;
}

interface CodeBlockProps {
  language: string;
  code: string;
}

/* ------------------------------------------------------------------ */
/*  Inline formatting helpers                                          */
/* ------------------------------------------------------------------ */

/**
 * Parse a single text string into an array of React nodes with
 * inline `code` and **bold** spans applied via CSS Modules.
 *
 * Uses a single combined regex so nested / overlapping markers are
 * handled in source order without dangerouslySetInnerHTML.
 */
function formatInline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const regex = /`([^`]+)`|\*\*([^*]+)\*\*/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = regex.exec(text)) !== null) {
    // Push any plain text before this match
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    if (match[1] !== undefined) {
      // Inline code
      parts.push(
        <code key={key++} className={styles.inlineCode}>
          {match[1]}
        </code>
      );
    } else if (match[2] !== undefined) {
      // Bold
      parts.push(
        <strong key={key++} className={styles.bold}>
          {match[2]}
        </strong>
      );
    }

    lastIndex = match.index + match[0].length;
  }

  // Remaining plain text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
}

/* ------------------------------------------------------------------ */
/*  CodeBlock                                                          */
/* ------------------------------------------------------------------ */

export function CodeBlock({ language, code }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  return (
    <div className={styles.codeBlock}>
      <div className={styles.codeHeader}>
        <span className={styles.codeLang}>{language || 'text'}</span>
        <button type="button" className={styles.copyBtn} onClick={handleCopy}>
          <svg
            className={styles.copyIcon}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            {copied ? (
              <path d="M20 6L9 17l-5-5" />
            ) : (
              <>
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
              </>
            )}
          </svg>
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      <div className={styles.codeContent}>
        <pre className={styles.code}>{code}</pre>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Prose block renderer (non-code content)                            */
/* ------------------------------------------------------------------ */

/**
 * Render a chunk of plain (non-fenced-code) text into headings,
 * bullet lists, and paragraphs.
 */
function renderProseBlock(block: string, blockKey: number): ReactNode[] {
  const nodes: ReactNode[] = [];

  // Split by double-newlines into paragraphs
  const paragraphs = block.split(/\n\n+/);

  for (let pi = 0; pi < paragraphs.length; pi++) {
    const para = paragraphs[pi].trim();
    if (!para) continue;

    const paraKey = `${blockKey}-${pi}`;
    const lines = para.split('\n');

    // Check if this paragraph is a heading: a single line that is exactly **text**
    if (lines.length === 1 && /^\*\*[^*]+\*\*$/.test(lines[0].trim())) {
      const headingText = lines[0].trim().slice(2, -2);
      nodes.push(
        <h4 key={paraKey} className={styles.heading}>
          {headingText}
        </h4>
      );
      continue;
    }

    // Check if all lines start with "- " (bullet list)
    const isList = lines.every(l => l.trimStart().startsWith('- '));

    if (isList) {
      nodes.push(
        <ul key={paraKey} className={styles.list}>
          {lines.map((line, li) => (
            <li key={`${paraKey}-li-${li}`} className={styles.listItem}>
              <span className={styles.listBullet}>&bull;</span>
              <span>{formatInline(line.trimStart().slice(2))}</span>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Regular paragraph
    nodes.push(
      <p key={paraKey} className={styles.paragraph}>
        {formatInline(para)}
      </p>
    );
  }

  return nodes;
}

/* ------------------------------------------------------------------ */
/*  RenderedContent                                                     */
/* ------------------------------------------------------------------ */

export function RenderedContent({ content, className }: RenderedContentProps) {
  // Split by outcome blocks first: ---outcome--- ... ---end--- (or standalone ---)
  const segments = content.split(OUTCOME_RE);

  const rendered: ReactNode[] = [];

  for (let si = 0; si < segments.length; si++) {
    const segment = segments[si];

    // Outcome block
    const outcomeMatch = segment.match(OUTCOME_EXTRACT_RE);
    if (outcomeMatch) {
      rendered.push(<OutcomeCard key={`outcome-${si}`} yaml={outcomeMatch[1]} />);
      continue;
    }

    // Split remaining content by fenced code blocks: ```lang\n...\n```
    const blocks = segment.split(/(```\w*\n[\s\S]*?```)/g);

    for (let i = 0; i < blocks.length; i++) {
      const block = blocks[i];

      const codeMatch = block.match(/^```(\w*)\n([\s\S]*?)```$/);
      if (codeMatch) {
        rendered.push(
          <CodeBlock
            key={`code-${si}-${i}`}
            language={codeMatch[1]}
            code={codeMatch[2].replace(/\n$/, '')}
          />
        );
        continue;
      }

      rendered.push(...renderProseBlock(block, si * 100 + i));
    }
  }

  return <div className={cn(styles.content, className)}>{rendered}</div>;
}
