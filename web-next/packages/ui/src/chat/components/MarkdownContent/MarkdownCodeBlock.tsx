import { useEffect, useState, Fragment } from 'react';
import { Copy, Check, ChevronRight, ChevronDown, WrapText } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';
import type { ThemedToken } from 'shiki';

interface CodeBlockProps {
  language?: string;
  code: string;
}

// A bounded cache keeps repeated transcript blocks cheap without retaining entire sessions.
const cache = new Map<string, ThemedToken[][]>();
function SyntaxTokens({ code, language }: CodeBlockProps) {
  const [result, setResult] = useState<{
    key: string;
    tokens?: ThemedToken[][];
    error?: boolean;
  }>();
  const key = `${language}\0${code}`;
  useEffect(() => {
    if (!language || code.length > 100_000) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const { codeToTokens, bundledLanguages } = await import('shiki');
          if (!(language in bundledLanguages)) return;
          let tokens = cache.get(key);
          if (!tokens) {
            tokens = (
              await codeToTokens(code, {
                lang: language as keyof typeof bundledLanguages,
                theme: 'github-dark',
              })
            ).tokens;
            if (cache.size >= 40) cache.delete(cache.keys().next().value!);
            cache.set(key, tokens);
          }
          if (!cancelled) setResult({ key, tokens });
        } catch {
          if (!cancelled) setResult({ key, error: true });
        }
      })();
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, code, language]);
  const current = result?.key === key ? result : undefined;
  // The pre/code DOM nodes never change as streamed source becomes highlighted.
  if (!current?.tokens)
    return (
      <>
        {code}
        {current?.error && (
          <span className="niuu-chat-md-highlight-warning" role="status">
            Syntax highlighting unavailable
          </span>
        )}
      </>
    );
  return (
    <>
      {current.tokens.map((line, index) => (
        <Fragment key={index}>
          {index > 0 && '\n'}
          {line.map((token, i) => (
            <span key={i} style={{ color: token.color }}>
              {token.content}
            </span>
          ))}
        </Fragment>
      ))}
    </>
  );
}

export function MarkdownCodeBlock({ language, code }: CodeBlockProps) {
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
          <code>
            <SyntaxTokens code={code} language={language} />
          </code>
        </pre>
      )}
    </div>
  );
}
