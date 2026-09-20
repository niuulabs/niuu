import { useId, useState } from 'react';
import { MarkdownContent } from '@niuulabs/ui';

export interface WorkflowResultsContextItem {
  label: string;
  value: string;
}

export interface WorkflowResultsProps {
  markdown: string;
  title: string;
  status: string;
  context?: readonly WorkflowResultsContextItem[];
  filename?: string;
  headingLevel?: 2 | 3;
  ariaLabel?: string;
}

function markdownFilename(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return `${normalized || 'workflow-results'}.md`;
}

export function WorkflowResults({
  markdown,
  title,
  status,
  context = [],
  filename,
  headingLevel = 2,
  ariaLabel,
}: WorkflowResultsProps) {
  const headingId = useId();
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const Heading = headingLevel === 2 ? 'h2' : 'h3';

  const copyMarkdown = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard is unavailable');
      await navigator.clipboard.writeText(markdown);
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  const downloadMarkdown = () => {
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename ?? markdownFilename(title);
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <section
      aria-label={ariaLabel}
      aria-labelledby={ariaLabel ? undefined : headingId}
      className="niuu:overflow-hidden niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary"
    >
      <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-4 niuu:border-b niuu:border-border niuu:p-5">
        <div className="niuu:space-y-2">
          <Heading
            id={headingId}
            className="niuu:text-lg niuu:font-semibold niuu:text-text-primary"
          >
            {title}
          </Heading>
          <span className="niuu:inline-flex niuu:rounded-full niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-secondary">
            {status}
          </span>
        </div>
        <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
          <button
            type="button"
            className="niuu:rounded niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
            onClick={() => void copyMarkdown()}
          >
            Copy Markdown
          </button>
          <button
            type="button"
            className="niuu:rounded niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
            onClick={downloadMarkdown}
          >
            Download Markdown
          </button>
        </div>
        <p className="niuu:sr-only" role="status" aria-live="polite">
          {copyState === 'copied'
            ? 'Markdown copied'
            : copyState === 'failed'
              ? 'Unable to copy Markdown'
              : ''}
        </p>
      </div>
      {context.length > 0 && (
        <dl className="niuu:grid niuu:gap-px niuu:border-b niuu:border-border niuu:bg-border niuu:sm:grid-cols-2 niuu:lg:grid-cols-4">
          {context.map((item) => (
            <div key={item.label} className="niuu:bg-bg-primary niuu:px-4 niuu:py-3">
              <dt className="niuu:text-xs niuu:font-medium niuu:uppercase niuu:tracking-wide niuu:text-text-secondary">
                {item.label}
              </dt>
              <dd className="niuu:mt-1 niuu:truncate niuu:text-sm niuu:text-text-primary">
                {item.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <article className="niuu:bg-bg-primary niuu:p-5 niuu:text-text-primary">
        <MarkdownContent content={markdown} />
      </article>
    </section>
  );
}
