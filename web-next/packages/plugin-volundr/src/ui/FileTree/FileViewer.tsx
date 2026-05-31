import { useState, useEffect } from 'react';

export interface FileViewerProps {
  /** Absolute path of the open file, displayed in the header. */
  path: string;
  /** Raw text content to display. */
  content: string;
  /** Called when the viewer should be closed. */
  onClose?: () => void;
  /** Whether content is still loading. */
  isLoading?: boolean;
  /** Error message if loading failed. */
  error?: string;
}

/**
 * Read-only file viewer with Shiki syntax highlighting.
 * Shiki is loaded lazily so the main bundle stays small.
 */
export function FileViewer({ path, content, onClose, isLoading, error }: FileViewerProps) {
  const language = detectLanguage(path);

  const basename = path.split('/').at(-1) ?? path;

  return (
    <div
      className="niuu-flex niuu-h-full niuu-flex-col niuu-overflow-hidden niuu-rounded-none niuu-border-l niuu-border-border-subtle niuu-bg-bg-secondary"
      data-testid="file-viewer"
      role="region"
      aria-label={`file viewer: ${basename}`}
    >
      {/* Header */}
      <div className="niuu-flex niuu-shrink-0 niuu-items-center niuu-gap-2 niuu-border-b niuu-border-border-subtle niuu-bg-bg-primary niuu-px-4 niuu-py-2.5">
        <span
          className="niuu-flex-1 niuu-truncate niuu-font-mono niuu-text-[12px] niuu-text-text-secondary"
          title={path}
          data-testid="file-viewer-path"
        >
          {path}
        </span>
        <span className="niuu-shrink-0 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
          {language}
        </span>
        {onClose && (
          <button
            className="niuu-ml-1 niuu-shrink-0 niuu-rounded-md niuu-px-2 niuu-py-0.5 niuu-font-mono niuu-text-[11px] niuu-text-text-muted hover:niuu-bg-bg-elevated hover:niuu-text-text-primary"
            onClick={onClose}
            aria-label="close file viewer"
            data-testid="file-viewer-close"
          >
            close
          </button>
        )}
      </div>

      {/* Body */}
      <div className="niuu-flex-1 niuu-overflow-auto">
        {isLoading && (
          <div
            className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted"
            role="status"
            data-testid="file-viewer-loading"
          >
            loading…
          </div>
        )}

        {error && (
          <div
            className="niuu-p-4 niuu-text-sm niuu-text-critical"
            role="alert"
            data-testid="file-viewer-error"
          >
            {error}
          </div>
        )}

        {!isLoading && !error && content && (
          <HighlightedContent key={`${path}:${content}`} content={content} language={language} />
        )}
      </div>
    </div>
  );
}

function HighlightedContent({ content, language }: { content: string; language: string }) {
  const [state, setState] = useState<{ html: string | null; warning: string | null }>({
    html: null,
    warning: null,
  });

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const { codeToHtml } = await import('shiki');
        const highlighted = await codeToHtml(content, {
          lang: language,
          theme: 'github-dark-dimmed',
        });
        if (!cancelled) {
          setState({ html: highlighted, warning: null });
        }
      } catch {
        if (!cancelled) {
          setState({ html: null, warning: 'Syntax highlighting unavailable' });
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [content, language]);

  return (
    <>
      {state.html ? (
        <div
          className="niuu-h-full niuu-overflow-auto niuu-p-0 niuu-text-xs [&_pre]:niuu-m-0 [&_pre]:niuu-h-full [&_pre]:niuu-overflow-auto [&_pre]:niuu-p-5"
          // Shiki renders safe, server-escaped HTML.
          dangerouslySetInnerHTML={{ __html: state.html }}
          data-testid="file-viewer-highlighted"
        />
      ) : (
        <pre
          className="niuu-m-0 niuu-overflow-auto niuu-p-5 niuu-font-mono niuu-text-xs niuu-text-text-secondary"
          data-testid="file-viewer-plain"
        >
          {content}
        </pre>
      )}

      {state.warning && (
        <p
          className="niuu-px-4 niuu-pt-0 niuu-text-xs niuu-text-text-muted"
          data-testid="file-viewer-highlight-warning"
        >
          {state.warning} — showing plain text.
        </p>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Language detection from file extension
// ---------------------------------------------------------------------------

const EXT_MAP: Record<string, string> = {
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  py: 'python',
  rs: 'rust',
  go: 'go',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'toml',
  md: 'markdown',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  css: 'css',
  html: 'html',
  sql: 'sql',
  dockerfile: 'dockerfile',
  tf: 'hcl',
  hcl: 'hcl',
  graphql: 'graphql',
  proto: 'protobuf',
  xml: 'xml',
  txt: 'text',
};

function detectLanguage(path: string): string {
  const basename = path.split('/').at(-1) ?? '';
  const lower = basename.toLowerCase();

  if (lower === 'dockerfile') return 'dockerfile';
  if (lower === 'makefile') return 'makefile';
  if (lower === '.env' || lower.startsWith('.env.')) return 'bash';

  const ext = lower.split('.').at(-1) ?? '';
  return EXT_MAP[ext] ?? 'text';
}
