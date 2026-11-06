import { useState, type FormEvent } from 'react';
import { ExternalLink, RotateCw } from 'lucide-react';
import { previewUrl, type LivePreviewConfig } from '../../domain/livePreview';
import './LivePreviewPane.css';

/**
 * A dev server the session runs, in the right dock: a URL bar for the path, the session's
 * open ports as chips, reload, and open in a new tab. The app runs on its own origin
 * (`<port>--<session>.<preview domain>`), so it never shares the platform's cookies or
 * tokens; the frame only embeds it.
 */
export function LivePreviewPane({
  sessionId,
  config,
  ports,
  port,
  initialPath = '/',
  onSelectPort,
}: {
  sessionId: string;
  config: LivePreviewConfig;
  /** The ports the session listens on now. */
  ports: readonly number[];
  /** The port shown; null until one is chosen or opens. */
  port: number | null;
  /** Where to open: a path the transcript linked to, say. Key the pane to open another. */
  initialPath?: string;
  onSelectPort: (port: number) => void;
}) {
  const [path, setPath] = useState(initialPath);
  const [draft, setDraft] = useState(initialPath);
  const [reloads, setReloads] = useState(0);
  const shown = port ?? ports[0] ?? null;
  const open = shown !== null && ports.includes(shown);
  const url = shown === null ? null : previewUrl(config, sessionId, shown, path);

  const go = (event: FormEvent) => {
    event.preventDefault();
    setPath(draft.trim() || '/');
    setReloads((count) => count + 1);
  };

  if (!config.enabled) {
    return (
      <div className="forge-preview-pane" data-testid="live-preview-pane">
        <p className="forge-preview-pane__empty">Live preview is not set up on this platform.</p>
      </div>
    );
  }

  return (
    <div className="forge-preview-pane" data-testid="live-preview-pane">
      <div className="forge-preview-pane__bar">
        <button
          type="button"
          className="forge-preview-pane__tool"
          aria-label="Reload"
          title="Reload"
          disabled={!open}
          onClick={() => setReloads((count) => count + 1)}
        >
          <RotateCw aria-hidden="true" />
        </button>
        <form className="forge-preview-pane__url" onSubmit={go}>
          <span
            className="forge-preview-pane__dot"
            data-open={open}
            aria-label={open ? 'Listening' : 'Not listening'}
          />
          {shown !== null && <b>:{shown}</b>}
          <input
            aria-label="Preview path"
            value={draft}
            spellCheck={false}
            disabled={shown === null}
            onChange={(event) => setDraft(event.target.value)}
          />
        </form>
        {ports.length > 0 && (
          <div className="forge-preview-pane__ports" role="group" aria-label="Open ports">
            {ports.map((candidate) => (
              <button
                key={candidate}
                type="button"
                className="forge-preview-pane__port"
                aria-pressed={candidate === shown}
                onClick={() => onSelectPort(candidate)}
              >
                {candidate}
              </button>
            ))}
          </div>
        )}
        {url && (
          <a
            className="forge-preview-pane__tool"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Open in a new tab"
            title="Open in a new tab"
          >
            <ExternalLink aria-hidden="true" />
          </a>
        )}
      </div>
      {shown === null && (
        <p className="forge-preview-pane__empty">
          Nothing in this session is listening yet. Start a dev server and it shows up here.
        </p>
      )}
      {shown !== null && !open && (
        <p className="forge-preview-pane__empty">Port {shown} stopped listening.</p>
      )}
      {open && url && (
        <iframe
          key={`${url}#${reloads}`}
          className="forge-preview-pane__frame"
          title={`Live preview of port ${shown}`}
          src={url}
          // Its own origin already isolates it; the sandbox keeps it from navigating this page.
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
        />
      )}
    </div>
  );
}
