import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { File, Image as ImageIcon } from 'lucide-react';
import type { ToolUseBlock } from './ToolBlock/groupContentBlocks';
import './ConversationResources.css';
import { ImagePreview } from './ImagePreview';
import { Tooltip, TooltipProvider } from '../../primitives/Tooltip/Tooltip';
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

export function isImageLink(href: string, mime?: string): boolean {
  if (mime?.startsWith('image/')) return true;
  return /\.(?:png|jpe?g|gif|webp|avif|svg|bmp|ico)$/i.test(href.split(/[?#]/)[0] ?? '');
}

/** Mounted only while the tooltip is visible; workspace bytes use the authenticated port. */
function ImageLinkThumbnail({
  href,
  resource,
  port,
}: {
  href: string;
  resource?: ConversationResource;
  port: ConversationResourcePort | null;
}) {
  const [url, setUrl] = useState(resource ? '' : href);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!resource || !port) return;
    const abort = new AbortController();
    let objectUrl: string | undefined;
    void port
      .load(resource, abort.signal)
      .then((blob) => {
        if (abort.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!abort.signal.aborted) setFailed(true);
      });
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [resource, port]);
  return (
    <span className="niuu-image-link-thumbnail">
      {failed ? (
        <span>Preview unavailable</span>
      ) : url ? (
        <img src={url} alt="" onError={() => setFailed(true)} />
      ) : (
        <span>Loading image…</span>
      )}
      <span>Click to view image</span>
    </span>
  );
}

export function ConversationLink({ href, children }: { href: string; children: ReactNode }) {
  const port = useContext(Context);
  const [imageOpen, setImageOpen] = useState(false);
  const resource = port?.resolve(href);
  const external = safeExternalUrl(href);
  const image =
    isImageLink(resource?.name ?? href, resource?.mime) &&
    (resource || (external && !external.startsWith('mailto:')));
  if (image) {
    const name = resource?.name ?? imageLinkName(href);
    return (
      <>
        <TooltipProvider>
          <Tooltip
            delayMs={150}
            ariaLabel="Image preview. Click to open."
            className="niuu-image-link-tooltip"
            content={
              <ImageLinkThumbnail href={href} resource={resource ?? undefined} port={port} />
            }
          >
            <button
              type="button"
              className="niuu-chat-md-link"
              onClick={() => (resource && port ? port.open(resource) : setImageOpen(true))}
            >
              {children}
            </button>
          </Tooltip>
        </TooltipProvider>
        {!resource && (
          <Dialog open={imageOpen} onOpenChange={setImageOpen}>
            <DialogContent title={name} className="niuu-chat-image-dialog">
              <ImagePreview src={href} originalUrl={href} name={name} />
            </DialogContent>
          </Dialog>
        )}
      </>
    );
  }
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

function imageLinkName(href: string): string {
  try {
    return decodeURIComponent(new URL(href).pathname.split('/').pop() ?? '') || 'Image preview';
  } catch {
    return 'Image preview';
  }
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
          <ImagePreview
            src={imageExternal ?? href}
            originalUrl={imageExternal ?? href}
            name={alt || imageLinkName(href)}
          />
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
