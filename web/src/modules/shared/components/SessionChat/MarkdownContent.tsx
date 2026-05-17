import React, { useCallback, useRef, useState, type ComponentPropsWithoutRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Copy, Check, WrapText } from 'lucide-react';
import { cn } from '@/utils';
import { OutcomeCard, OUTCOME_RE, OUTCOME_EXTRACT_RE } from './OutcomeCard';
import styles from './MarkdownContent.module.css';

const COLLAPSE_LINE_THRESHOLD = 25;

interface ArchivedSummaryPayload {
  readonly summary: string;
  readonly key_changes?: readonly string[];
  readonly unfinished_work?: readonly string[];
}

/* ------------------------------------------------------------------ */
/*  Code block with copy button                                        */
/* ------------------------------------------------------------------ */

interface CodeBlockRendererProps {
  language: string;
  code: string;
}

function CodeBlockRenderer({ language, code }: CodeBlockRendererProps) {
  const [copied, setCopied] = useState(false);
  const [collapsed, setCollapsed] = useState(true);
  const [wordWrap, setWordWrap] = useState(false);
  const [showLineNumbers, setShowLineNumbers] = useState(true);
  const blockRef = useRef<HTMLDivElement>(null);

  const lines = code.split('\n');
  const lineCount = lines.length;
  const isLong = lineCount > COLLAPSE_LINE_THRESHOLD;
  const shouldCollapse = isLong && collapsed;

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  const toggleCollapse = useCallback((value: boolean) => {
    // Preserve scroll position so expanding/collapsing doesn't jump the page
    const scrollParent = blockRef.current?.closest(
      '[class*="messagesContainer"]'
    ) as HTMLElement | null;
    const scrollTop = scrollParent?.scrollTop ?? 0;
    const blockTop = blockRef.current?.getBoundingClientRect().top ?? 0;

    setCollapsed(value);

    // After React re-renders, restore the block's position relative to viewport
    requestAnimationFrame(() => {
      if (!scrollParent || !blockRef.current) return;
      const newBlockTop = blockRef.current.getBoundingClientRect().top;
      const drift = newBlockTop - blockTop;
      scrollParent.scrollTop = scrollTop + drift;
    });
  }, []);

  const displayCode = shouldCollapse ? lines.slice(0, COLLAPSE_LINE_THRESHOLD).join('\n') : code;

  return (
    <div className={styles.codeBlock} ref={blockRef}>
      <div className={styles.codeHeader}>
        <span className={styles.codeLang}>{language || 'text'}</span>
        <div className={styles.codeHeaderActions}>
          <button
            type="button"
            className={styles.codeHeaderBtn}
            onClick={() => setShowLineNumbers(prev => !prev)}
            title={showLineNumbers ? 'Hide line numbers' : 'Show line numbers'}
            data-testid="toggle-line-numbers"
          >
            <span className={styles.codeHeaderBtnLabel}>{showLineNumbers ? '#' : '¶'}</span>
          </button>
          <button
            type="button"
            className={styles.codeHeaderBtn}
            onClick={() => setWordWrap(prev => !prev)}
            title={wordWrap ? 'Disable word wrap' : 'Enable word wrap'}
            data-active={wordWrap}
            data-testid="toggle-word-wrap"
          >
            <WrapText className={styles.copyIcon} />
          </button>
          <button type="button" className={styles.copyBtn} onClick={handleCopy}>
            {copied ? <Check className={styles.copyIcon} /> : <Copy className={styles.copyIcon} />}
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
      </div>
      <div
        className={cn(styles.codeContent, shouldCollapse && styles.codeCollapsed)}
        data-wrap={wordWrap}
      >
        <pre>
          <code className={showLineNumbers ? styles.codeContentGrid : undefined}>
            {showLineNumbers && (
              <span className={styles.lineNumbers} aria-hidden="true">
                {(shouldCollapse ? lines.slice(0, COLLAPSE_LINE_THRESHOLD) : lines).map((_, i) => (
                  <span key={i}>{i + 1}</span>
                ))}
              </span>
            )}
            <span>{displayCode}</span>
          </code>
        </pre>
        {shouldCollapse && <div className={styles.codeFade} />}
      </div>
      {shouldCollapse && (
        <button type="button" className={styles.showAllBtn} onClick={() => toggleCollapse(false)}>
          Show all {lineCount} lines
        </button>
      )}
      {isLong && !collapsed && (
        <button type="button" className={styles.showAllBtn} onClick={() => toggleCollapse(true)}>
          Collapse
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  MarkdownContent                                                     */
/* ------------------------------------------------------------------ */

interface MarkdownContentProps {
  content: string;
  isStreaming?: boolean;
  className?: string;
}

function parseArchivedSummary(raw: string): ArchivedSummaryPayload | null {
  const trimmed = raw.trim();
  if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) {
    return null;
  }
  try {
    const parsed = JSON.parse(trimmed) as Record<string, unknown>;
    const allowed = new Set(['summary', 'key_changes', 'unfinished_work']);
    if (!Object.keys(parsed).every(key => allowed.has(key))) {
      return null;
    }
    if (typeof parsed.summary !== 'string' || !parsed.summary.trim()) {
      return null;
    }

    const keyChanges =
      Array.isArray(parsed.key_changes) &&
      parsed.key_changes.every(item => typeof item === 'string' && item.trim().length > 0)
        ? (parsed.key_changes as string[])
        : undefined;

    const unfinished =
      parsed.unfinished_work === null || parsed.unfinished_work === undefined
        ? undefined
        : typeof parsed.unfinished_work === 'string'
          ? parsed.unfinished_work.trim()
            ? [parsed.unfinished_work]
            : undefined
          : Array.isArray(parsed.unfinished_work) &&
              parsed.unfinished_work.every(
                item => typeof item === 'string' && item.trim().length > 0
              )
            ? (parsed.unfinished_work as string[])
            : undefined;

    return {
      summary: parsed.summary.trim(),
      key_changes: keyChanges,
      unfinished_work: unfinished,
    };
  } catch {
    return null;
  }
}

function SummaryMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
      {content}
    </ReactMarkdown>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const markdownComponents: Record<string, React.ComponentType<any>> = {
  p: ({ children }: { children?: React.ReactNode }) => (
    <p className={styles.paragraph}>{children}</p>
  ),
  h1: ({ children }: { children?: React.ReactNode }) => <h1 className={styles.h1}>{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) => <h2 className={styles.h2}>{children}</h2>,
  h3: ({ children }: { children?: React.ReactNode }) => <h3 className={styles.h3}>{children}</h3>,
  code: ({
    className: codeClassName,
    children,
    ...rest
  }: ComponentPropsWithoutRef<'code'> & { className?: string }) => {
    const match = /language-(\w+)/.exec(codeClassName ?? '');
    const codeString = String(children).replace(/\n$/, '');

    if (match) {
      return <CodeBlockRenderer language={match[1]} code={codeString} />;
    }

    const node = (rest as Record<string, unknown>).node as
      | { position?: { start?: { line?: number }; end?: { line?: number } } }
      | undefined;
    const isMultiLine =
      node?.position?.start?.line !== undefined &&
      node?.position?.end?.line !== undefined &&
      node.position.end.line > node.position.start.line;

    if (isMultiLine) {
      return <CodeBlockRenderer language="" code={codeString} />;
    }

    return <code className={styles.inlineCode}>{children}</code>;
  },
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className={styles.tableWrapper}>
      <table className={styles.table}>{children}</table>
    </div>
  ),
  thead: ({ children }: { children?: React.ReactNode }) => (
    <thead className={styles.thead}>{children}</thead>
  ),
  th: ({ children }: { children?: React.ReactNode }) => <th className={styles.th}>{children}</th>,
  td: ({ children }: { children?: React.ReactNode }) => <td className={styles.td}>{children}</td>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul className={styles.ul}>{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol className={styles.ol}>{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li className={styles.li}>{children}</li>,
  blockquote: ({ children }: { children?: React.ReactNode }) => (
    <blockquote className={styles.blockquote}>{children}</blockquote>
  ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className={styles.link}>
      {children}
    </a>
  ),
  pre: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  strong: ({ children }: { children?: React.ReactNode }) => (
    <strong className={styles.strong}>{children}</strong>
  ),
  em: ({ children }: { children?: React.ReactNode }) => <em className={styles.em}>{children}</em>,
  hr: () => <hr className={styles.hr} />,
};

/* ------------------------------------------------------------------ */
/*  MarkdownContent                                                     */
/* ------------------------------------------------------------------ */

export function MarkdownContent({ content, isStreaming, className }: MarkdownContentProps) {
  const segments = content.split(OUTCOME_RE);

  return (
    <div className={cn(styles.content, className)}>
      {segments.map((segment, i) => {
        const match = segment.match(OUTCOME_EXTRACT_RE);
        if (match) {
          return <OutcomeCard key={`outcome-${i}`} yaml={match[1]} />;
        }
        if (!segment.trim()) return null;
        const archivedSummary = parseArchivedSummary(segment);
        if (archivedSummary) {
          const unfinished =
            archivedSummary.unfinished_work && archivedSummary.unfinished_work.length > 0
              ? archivedSummary.unfinished_work
              : null;
          return (
            <section key={`summary-${i}`} className={styles.summaryCard}>
              <div className={styles.summaryEyebrow}>Session summary</div>
              <div className={styles.summaryBody}>
                <SummaryMarkdown content={archivedSummary.summary} />
              </div>
              {archivedSummary.key_changes && archivedSummary.key_changes.length > 0 && (
                <div className={styles.summarySection}>
                  <div className={styles.summaryHeading}>Key changes</div>
                  <ul className={styles.summaryList}>
                    {archivedSummary.key_changes.map(item => (
                      <li key={item} className={styles.summaryListItem}>
                        <SummaryMarkdown content={item} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {unfinished && (
                <div className={styles.summarySection}>
                  <div className={styles.summaryHeading}>Unfinished work</div>
                  <ul className={styles.summaryList}>
                    {unfinished.map(item => (
                      <li key={item} className={styles.summaryListItem}>
                        <SummaryMarkdown content={item} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          );
        }
        return (
          <ReactMarkdown
            key={`md-${i}`}
            remarkPlugins={[remarkGfm]}
            components={markdownComponents}
          >
            {segment}
          </ReactMarkdown>
        );
      })}
      {isStreaming && <span className={styles.streamingCursor} />}
    </div>
  );
}
