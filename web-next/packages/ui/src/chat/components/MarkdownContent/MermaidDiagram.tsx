import { useEffect, useId, useState } from 'react';
import { Expand, Code, ChartNoAxesCombined } from 'lucide-react';
import { Dialog, DialogContent } from '../../../primitives/Dialog/Dialog';
import { MarkdownCodeBlock } from './MarkdownCodeBlock';

let renderQueue: Promise<unknown> = Promise.resolve();

export function MermaidDiagram({
  source,
  isStreaming = false,
}: {
  source: string;
  isStreaming?: boolean;
}) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '');
  const [result, setResult] = useState<{ source: string; url?: string; error?: string }>();
  const [showSource, setShowSource] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const current = result?.source === source ? result : undefined;
  useEffect(() => {
    if (isStreaming || source.length > 50_000) return;
    let cancelled = false;
    let objectUrl: string | undefined;
    const timer = setTimeout(() => {
      const task = renderQueue.then(async () => {
        if (cancelled) return;
        const [{ default: mermaid }, { default: purify }] = await Promise.all([
          import('mermaid'),
          import('dompurify'),
        ]);
        const styles = getComputedStyle(document.documentElement);
        const token = (name: string) => styles.getPropertyValue(name).trim();
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          suppressErrorRendering: true,
          theme: 'base',
          themeVariables: {
            darkMode: true,
            background: token('--color-bg-secondary'),
            primaryColor: token('--color-bg-tertiary'),
            primaryTextColor: token('--color-text-primary'),
            primaryBorderColor: token('--color-brand'),
            lineColor: token('--color-text-secondary'),
            secondaryColor: token('--color-bg-elevated'),
            tertiaryColor: token('--color-bg-primary'),
            fontFamily: token('--font-sans'),
          },
          flowchart: { htmlLabels: false },
          maxTextSize: 50_000,
        });
        const { svg } = await mermaid.render(`niuu-mermaid-${id}`, source);
        if (cancelled) return;
        const safe = purify.sanitize(svg, {
          USE_PROFILES: { svg: true, svgFilters: true },
          FORBID_TAGS: ['foreignObject', 'script', 'iframe', 'object', 'embed'],
          ALLOW_DATA_ATTR: false,
        });
        objectUrl = URL.createObjectURL(new Blob([safe], { type: 'image/svg+xml' }));
        setResult({ source, url: objectUrl });
      });
      renderQueue = task.catch(() => {});
      void task.catch((error: unknown) => {
        if (!cancelled)
          setResult({
            source,
            error: error instanceof Error ? error.message : 'Diagram could not be rendered',
          });
      });
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id, source, isStreaming]);
  const pending = isStreaming || source.length > 50_000;
  return (
    <div className="niuu-chat-md-diagram">
      <div className="niuu-chat-md-diagram-toolbar">
        <span>
          <ChartNoAxesCombined size={16} /> Diagram
        </span>
        <button type="button" onClick={() => setShowSource(!showSource)} aria-pressed={showSource}>
          <Code size={16} /> {showSource ? 'Preview' : 'Source'}
        </button>
        {current?.url && (
          <button type="button" onClick={() => setExpanded(true)} aria-label="Expand diagram">
            <Expand size={16} />
          </button>
        )}
      </div>
      {current?.error && (
        <p role="status" className="niuu-chat-md-diagram-error">
          Diagram preview unavailable. The source is shown below.
        </p>
      )}
      {pending || showSource || current?.error ? (
        <MarkdownCodeBlock language="mermaid" code={source} />
      ) : current?.url ? (
        <button
          type="button"
          className="niuu-chat-md-diagram-image"
          onClick={() => setExpanded(true)}
          aria-label="Open diagram preview"
        >
          <img src={current.url} alt="Mermaid diagram" />
        </button>
      ) : (
        <p role="status">Rendering diagram…</p>
      )}
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent title="Diagram preview" className="niuu-chat-md-diagram-dialog">
          <img src={current?.url} alt="Expanded Mermaid diagram" />
        </DialogContent>
      </Dialog>
    </div>
  );
}
