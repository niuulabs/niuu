import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { File, Image as ImageIcon } from 'lucide-react';
import type { ToolUseBlock } from './ToolBlock/groupContentBlocks';
import './ConversationResources.css';
import { Dialog, DialogContent } from '../../primitives/Dialog/Dialog';

export interface ConversationResource {
  kind: 'workspace' | 'presented';
  path: string;
  name: string;
  mime?: string;
}

export interface ConversationResourcePort {
  resolve(href: string): ConversationResource | null;
  load(resource: ConversationResource, signal: AbortSignal): Promise<Blob>;
  open(resource: ConversationResource): void;
}

const Context = createContext<ConversationResourcePort | null>(null);
export function ConversationResourceProvider({
  port,
  children,
}: {
  port: ConversationResourcePort;
  children: ReactNode;
}) {
  return <Context.Provider value={port}>{children}</Context.Provider>;
}

export function useConversationResources() {
  return useContext(Context);
}

export function safeExternalUrl(href: string): string | null {
  try {
    const url = new URL(href);
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? href : null;
  } catch {
    return null;
  }
}

export function ConversationLink({ href, children }: { href: string; children: ReactNode }) {
  const port = useContext(Context);
  const resource = port?.resolve(href);
  if (resource && port)
    return (
      <button
        type="button"
        className="niuu-chat-md-link"
        onClick={() => port.open(resource)}
        title={resource.path}
      >
        {children}
      </button>
    );
  const external = safeExternalUrl(href);
  if (external)
    return (
      <a href={external} className="niuu-chat-md-link" target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  if (href.startsWith('#'))
    return (
      <a href={href} className="niuu-chat-md-link">
        {children}
      </a>
    );
  return <span title="This link is not available in the current session">{children}</span>;
}

export function ConversationImage({ href, alt }: { href: string; alt: string }) {
  const [imageOpen, setImageOpen] = useState(false);
  const port = useContext(Context);
  const [loaded, setLoaded] = useState<{
    href: string;
    port: ConversationResourcePort | null;
    url?: string;
    error?: string;
  }>();
  const current = loaded?.href === href && loaded.port === port ? loaded : undefined;
  const localUrl = current?.url;
  const error = current?.error;
  const external = safeExternalUrl(href);
  const imageExternal = external && !external.startsWith('mailto:') ? external : null;
  const resource = port?.resolve(href);
  const resourcePath = resource?.path;

  useEffect(() => {
    if (!port || !resourcePath) return;
    const current = port.resolve(href);
    if (!current) return;
    const abort = new AbortController();
    let objectUrl: string | undefined;
    void port
      .load(current, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setLoaded({ href, port, url: objectUrl });
      })
      .catch((error: unknown) => {
        if (!abort.signal.aborted)
          setLoaded({
            href,
            port,
            error: error instanceof Error ? error.message : 'Image unavailable',
          });
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [port, href, resourcePath]);

  const src = imageExternal ?? localUrl;
  if (error)
    return (
      <span role="status" className="niuu-chat-resource-error">
        {alt || 'Image'}: {error}
      </span>
    );
  if (!src)
    return (
      <span className="niuu-chat-resource-loading">
        <ImageIcon size={16} />
        {resource ? 'Loading image…' : alt || 'Image unavailable'}
      </span>
    );
  const content = (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setLoaded({ href, port, error: 'Could not load image' })}
    />
  );
  if (resource && port)
    return (
      <button
        type="button"
        className="niuu-chat-resource-image"
        aria-label={`Open image ${alt || resource.name}`}
        onClick={() => port.open(resource)}
      >
        {content}
      </button>
    );
  return (
    <>
      <button
        type="button"
        className="niuu-chat-resource-image"
        aria-label={`Open image ${alt || 'preview'}`}
        onClick={() => setImageOpen(true)}
      >
        {content}
      </button>
      <Dialog open={imageOpen} onOpenChange={setImageOpen}>
        <DialogContent title={alt || 'Image preview'} className="niuu-chat-image-dialog">
          <img src={imageExternal ?? undefined} alt={alt} />
          <a
            className="niuu-chat-md-link"
            href={imageExternal ?? undefined}
            target="_blank"
            rel="noreferrer"
          >
            Open original image
          </a>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function PresentedFileCard({ block }: { block: ToolUseBlock }) {
  const port = useContext(Context);
  const fileId = typeof block.input.file_id === 'string' ? block.input.file_id : '';
  const name = typeof block.input.name === 'string' ? block.input.name : 'Delivered file';
  const mime = typeof block.input.mime === 'string' ? block.input.mime : undefined;
  const caption = typeof block.input.caption === 'string' ? block.input.caption : undefined;
  return (
    <div className="niuu-chat-presented-file" data-testid="presented-file-card">
      <File size={20} />
      <div>
        <strong>{name}</strong>
        {caption && <p>{caption}</p>}
        <button
          type="button"
          disabled={!port || !fileId}
          onClick={() => port?.open({ kind: 'presented', path: fileId, name, mime })}
        >
          Open file
        </button>
        {!fileId && <span role="status">File delivery is incomplete.</span>}
        {!port && <span role="status">File access is unavailable in this view.</span>}
      </div>
    </div>
  );
}
