import { useEffect, useMemo, useState } from 'react';
import { Copy, Download, ExternalLink } from 'lucide-react';
import { ImagePreview } from './ImagePreview';
import { MarkdownContent } from './MarkdownContent';
import {
  ConversationResourceProvider,
  externalResource,
  isImageLink,
  safeExternalUrl,
  useConversationResources,
} from './ConversationResources';

/** External links stay in the same viewport; navigation out is always an explicit action. */
export function ExternalLinkPreview({ href, name }: { href: string; name: string }) {
  const parent = useConversationResources();
  const [location, setLocation] = useState({ href, name });
  const port = useMemo(
    () => ({
      resolve: (target: string) => {
        if (target.startsWith('#')) return null;
        try {
          return externalResource(new URL(target, location.href).href);
        } catch {
          return null;
        }
      },
      load: async (resource: { path: string }, signal: AbortSignal) => {
        const response = await fetch(resource.path, { signal, credentials: 'omit' });
        if (!response.ok) throw new Error(`Could not load file (${response.status})`);
        return response.blob();
      },
      open: parent
        ? parent.open
        : (resource: { path: string; name: string }) =>
            setLocation({ href: resource.path, name: resource.name }),
    }),
    [parent, location.href],
  );
  return (
    <ConversationResourceProvider port={port}>
      <LinkPreviewContent key={location.href} href={location.href} name={location.name} />
    </ConversationResourceProvider>
  );
}

function LinkPreviewContent({ href, name }: { href: string; name: string }) {
  const url = safeExternalUrl(href);
  const isMail = url?.toLowerCase().startsWith('mailto:');
  const isMarkdown = /\.(?:md|markdown|mdown)(?:[?#]|$)/i.test(href);
  const [document, setDocument] = useState<{ text: string; download: string }>();
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  useEffect(() => {
    if (!url || !isMarkdown || isMail) return;
    const abort = new AbortController();
    let download: string | undefined;
    void fetch(url, { signal: abort.signal, credentials: 'omit', referrerPolicy: 'no-referrer' })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Could not load document (${response.status}).`);
        const blob = await response.blob();
        if (blob.size > 2 * 1024 * 1024)
          throw new Error('This document is larger than the 2 MiB preview limit.');
        const text = await blob.text();
        if (abort.signal.aborted) return;
        download = URL.createObjectURL(blob);
        setDocument({ text, download });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setError(error instanceof Error ? error.message : 'Could not load document.');
      });
    return () => {
      abort.abort();
      if (download) URL.revokeObjectURL(download);
    };
  }, [url, isMarkdown, isMail]);

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback('Copied');
    } catch {
      setFeedback('Could not copy. Select and copy the link below.');
    }
  }
  if (!url) return <p role="alert">This link cannot be previewed.</p>;
  if (!isMail && isImageLink(href)) return <ImagePreview src={url} originalUrl={url} name={name} />;
  return (
    <div className="niuu-link-preview">
      <div className="niuu-link-preview-toolbar">
        <button type="button" onClick={() => void copy(url)}>
          <Copy size={16} />
          Copy link
        </button>
        {document && (
          <button type="button" onClick={() => void copy(document.text)}>
            <Copy size={16} />
            Copy text
          </button>
        )}
        {document && (
          <a href={document.download} download={name}>
            <Download size={16} />
            Download
          </a>
        )}
        <a href={url} target="_blank" rel="noopener noreferrer">
          <ExternalLink size={16} />
          {isMail ? 'Open mail app' : 'Open in new tab'}
        </a>
        <span role="status">{feedback}</span>
      </div>
      {isMail ? (
        <div className="niuu-link-preview-message">
          <p>Email link</p>
          <p>{url.slice(7)}</p>
        </div>
      ) : isMarkdown ? (
        document ? (
          <article className="niuu-link-preview-document">
            <MarkdownContent content={document.text} />
          </article>
        ) : (
          <div className="niuu-link-preview-message">
            {error ? (
              <p role="alert">
                {error} The site may restrict access to its content. You can open the original
                above.
              </p>
            ) : (
              <p role="status">Loading document…</p>
            )}
          </div>
        )
      ) : (
        <>
          <p className="niuu-link-preview-hint">
            If this site restricts embedded previews, use “Open in new tab”.
          </p>
          <iframe
            title={`Preview of ${name}`}
            src={url}
            sandbox="allow-scripts allow-forms"
            referrerPolicy="no-referrer"
          />
        </>
      )}
      <div className="niuu-link-preview-footer">{url}</div>
    </div>
  );
}
