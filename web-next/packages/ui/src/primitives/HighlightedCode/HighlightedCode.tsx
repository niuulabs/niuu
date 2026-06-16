import { useEffect, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useCopyFeedback } from '../../chat/hooks/useCopyFeedback';
import { cn } from '../../utils/cn';

export interface HighlightedCodeProps {
  /** The source to render. */
  code: string;
  /** A shiki language id (e.g. 'python', 'json', 'bash'). Defaults to plain text. */
  lang?: string;
  /** Extra classes for the outer container. */
  className?: string;
  testId?: string;
}

/**
 * Read-only code viewer: shiki syntax highlighting with a copy button, and a
 * graceful plain-text fallback while highlighting loads or if it fails. Shiki
 * is imported lazily so it only ships in the chunk that renders code.
 */
export function HighlightedCode({ code, lang = 'text', className, testId }: HighlightedCodeProps) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, copy] = useCopyFeedback(code);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { codeToHtml } = await import('shiki');
        const out = await codeToHtml(code, { lang, theme: 'github-dark-dimmed' });
        if (!cancelled) setHtml(out);
      } catch {
        if (!cancelled) setHtml(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  return (
    <div className={cn('niuu:relative', className)} data-testid={testId}>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? 'Copied' : 'Copy'}
        className="niuu:absolute niuu:right-2 niuu:top-2 niuu:z-10 niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:hover:border-brand"
      >
        {copied ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
        {copied ? 'Copied' : 'Copy'}
      </button>
      {html ? (
        <div
          className="niuu:max-h-[26rem] niuu:overflow-auto niuu:rounded-md niuu:text-xs [&niuu:_pre]:m-0 [&niuu:_pre]:overflow-auto [&niuu:_pre]:p-3"
          // Shiki renders safe, server-escaped HTML.
          dangerouslySetInnerHTML={{ __html: html }}
          data-testid={testId ? `${testId}-highlighted` : undefined}
        />
      ) : (
        <pre
          className="niuu:max-h-[26rem] niuu:overflow-auto niuu:rounded-md niuu:bg-bg-primary niuu:p-3 niuu:font-mono niuu:text-xs niuu:text-text-secondary niuu:whitespace-pre-wrap"
          data-testid={testId ? `${testId}-plain` : undefined}
        >
          {code}
        </pre>
      )}
    </div>
  );
}
