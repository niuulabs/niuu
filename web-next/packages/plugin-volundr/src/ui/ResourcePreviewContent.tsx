import { useState } from 'react';
import { Download, Copy, Check, FileText } from 'lucide-react';
import {
  ImagePreview,
  MarkdownContent,
  MarkdownCodeBlock,
  MermaidDiagram,
  useCopyFeedback,
  type ConversationResource,
} from '@niuulabs/ui';
import { isTextPreview, type PreviewKind } from '../domain/filePreview';

export interface LoadedPreview {
  url: string;
  blob: Blob;
  text?: string;
  kind: PreviewKind;
  language?: string;
}

export function ResourcePreviewContent({
  resource,
  preview,
}: {
  resource: ConversationResource;
  preview: LoadedPreview;
}) {
  const [source, setSource] = useState(false);
  const [mediaError, setMediaError] = useState(false);
  const [copied, copy] = useCopyFeedback(preview.text ?? '');
  const [pathCopied, copyPath] = useCopyFeedback(resource.path);
  const hasRenderedView = ['markdown', 'mermaid', 'html'].includes(preview.kind);
  const size =
    preview.blob.size < 1024
      ? `${preview.blob.size} B`
      : preview.blob.size < 1024 * 1024
        ? `${(preview.blob.size / 1024).toFixed(1)} KB`
        : `${(preview.blob.size / 1024 / 1024).toFixed(1)} MB`;
  if (preview.kind === 'image')
    return <ImagePreview src={preview.url} name={resource.name} blob={preview.blob} />;
  return (
    <>
      <div className="forge-resource-toolbar" aria-label="File preview controls">
        <span className="forge-resource-kind">
          <FileText size={16} />
          {preview.kind} <span>· {size}</span>
        </span>
        {hasRenderedView && preview.text !== undefined && (
          <div className="forge-resource-modes">
            <button type="button" aria-pressed={!source} onClick={() => setSource(false)}>
              Preview
            </button>
            <button type="button" aria-pressed={source} onClick={() => setSource(true)}>
              Source
            </button>
          </div>
        )}
        <div className="forge-resource-toolbar__actions">
          {preview.text !== undefined && (
            <button type="button" onClick={copy}>
              {copied ? <Check size={16} /> : <Copy size={16} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
          <a href={preview.url} download={resource.name} aria-label={`Download ${resource.name}`}>
            <Download size={16} />
            Download
          </a>
        </div>
      </div>
      <div className={`forge-resource-preview forge-resource-preview--${preview.kind}`}>
        {preview.kind === 'pdf' && <iframe title={resource.name} src={preview.url} />}
        {preview.kind === 'video' && (
          <video
            src={preview.url}
            controls
            preload="metadata"
            aria-label={resource.name}
            onError={() => setMediaError(true)}
          />
        )}
        {preview.kind === 'audio' && (
          <div className="forge-resource-media">
            <FileText size={48} />
            <h3>{resource.name}</h3>
            <audio
              src={preview.url}
              controls
              preload="metadata"
              aria-label={resource.name}
              onError={() => setMediaError(true)}
            />
          </div>
        )}
        {mediaError && (
          <p role="alert">
            This browser could not preview the file. Use Download to open it in another application.
          </p>
        )}
        {preview.text !== undefined &&
          (source || preview.kind === 'code' || preview.kind === 'text' ? (
            <MarkdownCodeBlock language={preview.language} code={preview.text} />
          ) : preview.kind === 'markdown' ? (
            <article className="forge-resource-document">
              <MarkdownContent content={preview.text} />
            </article>
          ) : preview.kind === 'mermaid' ? (
            <MermaidDiagram source={preview.text} />
          ) : preview.kind === 'html' ? (
            <iframe
              title={resource.name}
              sandbox=""
              referrerPolicy="no-referrer"
              srcDoc={`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:">${preview.text}`}
            />
          ) : null)}
        {isTextPreview(preview.kind) && preview.text === undefined && (
          <div className="forge-resource-empty">
            <FileText size={40} />
            <p>
              This file is larger than the 2 MiB text preview limit. Download it to read the full
              file.
            </p>
          </div>
        )}
        {preview.kind === 'download' && (
          <div className="forge-resource-empty">
            <FileText size={40} />
            <h3>{resource.name}</h3>
            <p>Download this file to open it in its application.</p>
          </div>
        )}
      </div>
      <footer className="forge-resource-footer">
        <span title={resource.path}>{resource.path}</span>
        <button type="button" onClick={copyPath} aria-label="Copy file path">
          {pathCopied ? <Check size={15} /> : <Copy size={15} />}
        </button>
      </footer>
    </>
  );
}
