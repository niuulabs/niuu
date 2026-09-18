import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { Image as ImageIcon, Loader2, RotateCcw } from 'lucide-react';
import { ToolImageLoader, loadToolImage } from '../toolImageLoader';
import type { ToolImage } from '../toolImages';
import { useBlobUrl } from '../useBlobUrl';
import { useConversationResources, type ToolImageResource } from './ConversationResources';
import { ToolImagePreview } from './ToolImagePreview';
import { Dialog, DialogContent } from '../../primitives/Dialog/Dialog';
import './ToolImages.css';

const Context = createContext<ToolImageLoader | null>(null);
export function ToolImageProvider({
  endpoint,
  children,
}: {
  endpoint: string | null;
  children: ReactNode;
}) {
  const loader = useMemo(() => new ToolImageLoader(endpoint), [endpoint]);
  useEffect(() => () => loader.dispose(), [loader]);
  return <Context.Provider value={loader}>{children}</Context.Provider>;
}

/** Fixed at mount: late image arrival must not move the reader's current paragraph. */
export function toolImageSize(image: ToolImage) {
  const width = image.width ?? 800,
    height = image.height ?? 600;
  const scale = Math.min(1, 200 / height, 400 / width);
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

export function ToolImageCard({ image }: { image: ToolImage }) {
  const context = useContext(Context);
  const local = useMemo(() => new ToolImageLoader(null), []);
  const loader = context ?? local;
  useEffect(() => () => local.dispose(), [local]);
  const resources = useConversationResources();
  const card = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const thumbnail = useRef<SVGImageElement>(null);
  const latest = useRef(image);
  useEffect(() => {
    latest.current = image;
  }, [image]);
  const [visible, setVisible] = useState(typeof IntersectionObserver === 'undefined');
  const [size] = useState(() => toolImageSize(image));
  const [selected, setSelected] = useState<ToolImageResource | null>(null);
  const [failedUrl, setFailedUrl] = useState('');
  useEffect(() => {
    if (!card.current || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(Boolean(entry?.isIntersecting)),
      { rootMargin: '300px' },
    );
    observer.observe(card.current);
    return () => observer.disconnect();
  }, []);
  const identity = `${image.toolUseId}:${image.index}`;
  const subscribe = useCallback(
    (listener: () => void) => (visible ? loader.subscribe(latest.current, listener) : () => {}),
    [loader, visible],
  );
  const snapshot = useCallback(() => loader.snapshot(latest.current), [loader]);
  const state = useSyncExternalStore(subscribe, snapshot, snapshot);
  const blob = visible ? state.blob : undefined;
  const url = useBlobUrl(blob);
  const failed = state.status === 'error' || Boolean(url && failedUrl === url);
  useEffect(() => {
    const element = thumbnail.current;
    const markFailed = () => setFailedUrl(url);
    element?.addEventListener('error', markFailed);
    return () => element?.removeEventListener('error', markFailed);
  }, [url, failed]);
  function open() {
    const resource: ToolImageResource = {
      kind: 'tool-image',
      path: identity,
      name: image.name,
      mime: image.mime,
      description: image.path ?? 'Image returned by this session’s tool',
      preview: blob,
      loadFull: (signal) => loadToolImage(image, loader.endpoint, signal),
    };
    if (resources) resources.open(resource);
    else setSelected(resource);
  }
  return (
    <div
      className="niuu-tool-image"
      ref={card}
      data-testid="tool-image-card"
      data-tool-id={image.toolUseId}
    >
      <button
        type="button"
        ref={trigger}
        className="niuu-tool-image-button"
        aria-label={`Open image ${image.name}`}
        title={image.path ?? image.name}
        onClick={open}
      >
        <svg
          width={size.width}
          height={size.height}
          viewBox={`0 0 ${size.width} ${size.height}`}
          aria-hidden="true"
        >
          {url && !failed && (
            <image
              href={url}
              ref={thumbnail}
              width={size.width}
              height={size.height}
              preserveAspectRatio="xMidYMid meet"
            />
          )}
        </svg>
        {(!url || failed) && (
          <span className="niuu-tool-image-placeholder">
            {failed ? (
              <ImageIcon size={24} />
            ) : (
              <Loader2 size={20} className="niuu-chat-spinner-icon" />
            )}
            <span>{failed ? 'Open image' : 'Loading preview…'}</span>
          </span>
        )}
        <span className="niuu-tool-image-caption">{image.name}</span>
      </button>
      {failed && (
        <button
          type="button"
          className="niuu-tool-image-retry"
          title={state.error ?? 'Could not decode thumbnail.'}
          onClick={() => loader.retry(image)}
        >
          <RotateCcw size={14} />
          Retry thumbnail
        </button>
      )}
      {!resources && selected && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) setSelected(null);
          }}
        >
          <DialogContent
            title={selected.name}
            description={selected.description}
            className="niuu-chat-image-dialog"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              trigger.current?.focus();
            }}
          >
            <ToolImagePreview resource={selected} />
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
