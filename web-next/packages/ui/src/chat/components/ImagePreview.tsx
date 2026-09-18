import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { Copy, Download, ExternalLink, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { imageAsPng, loadImageBlob, downloadImageBlob } from './imageActions';
import './ImagePreview.css';

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 8;
const ZOOM_STEP = 1.25;
interface View {
  zoom: number;
  x: number;
  y: number;
}
const FIT: View = { zoom: 1, x: 0, y: 0 };

export function ImagePreview({
  src,
  name,
  blob,
  originalUrl,
  transferDisabled = false,
}: {
  src: string;
  name: string;
  blob?: Blob;
  originalUrl?: string;
  transferDisabled?: boolean;
}) {
  const canvas = useRef<HTMLDivElement>(null);
  const imageElement = useRef<SVGImageElement>(null);
  const [size, setSize] = useState({ width: 1000, height: 700 });
  const [view, setView] = useState<View>(FIT);
  const [error, setError] = useState('');
  const [failedSrc, setFailedSrc] = useState('');
  const imageFailed = failedSrc === src;
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useRef<AbortController | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());

  useEffect(() => {
    const image = imageElement.current;
    const failed = () => setFailedSrc(image?.getAttribute('href') ?? '');
    image?.addEventListener('error', failed);
    const element = canvas.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry && entry.contentRect.width > 0 && entry.contentRect.height > 0)
        setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      setView((current) =>
        zoomView(
          current,
          event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP,
          event.clientX - rect.left,
          event.clientY - rect.top,
        ),
      );
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => {
      image?.removeEventListener('error', failed);
      observer.disconnect();
      element.removeEventListener('wheel', wheel);
      operation.current?.abort();
    };
  }, []);

  function zoom(factor: number) {
    setView((current) => zoomView(current, factor, size.width / 2, size.height / 2));
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const before = pointers.current.get(event.pointerId);
    if (!before) return;
    const oldPoints = [...pointers.current.values()];
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointers.current.values()];
    const first = points[0],
      second = points[1],
      oldFirst = oldPoints[0],
      oldSecond = oldPoints[1];
    if (first && second && oldFirst && oldSecond) {
      const oldDistance = Math.hypot(oldFirst.x - oldSecond.x, oldFirst.y - oldSecond.y);
      const distance = Math.hypot(first.x - second.x, first.y - second.y);
      const rect = event.currentTarget.getBoundingClientRect();
      if (oldDistance > 0)
        setView((current) =>
          zoomView(
            current,
            distance / oldDistance,
            (first.x + second.x) / 2 - rect.left,
            (first.y + second.y) / 2 - rect.top,
          ),
        );
    } else
      setView((current) => ({
        ...current,
        x: current.x + event.clientX - before.x,
        y: current.y + event.clientY - before.y,
      }));
  }

  async function transfer(action: 'copy' | 'download') {
    if (busy) return;
    const abort = new AbortController();
    operation.current = abort;
    setBusy(true);
    setError('');
    setStatus('');
    try {
      const bytes = () => (blob ? Promise.resolve(blob) : loadImageBlob(src, abort.signal));
      if (action === 'copy') {
        if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined')
          throw new Error(
            'Image copying is unavailable in this browser. You can still download the image.',
          );
        // Start clipboard.write in the click gesture (including Safari); resolve PNG bytes asynchronously.
        await navigator.clipboard.write([
          new ClipboardItem({ 'image/png': bytes().then(imageAsPng) }),
        ]);
      } else {
        const image = await bytes();
        if (!abort.signal.aborted) downloadImageBlob(image, name);
      }
      if (!abort.signal.aborted) setStatus(action === 'copy' ? 'Image copied' : 'Download started');
    } catch (error) {
      if (!abort.signal.aborted)
        setError(
          error instanceof TypeError
            ? 'This image host does not allow copying or downloading here. Use Open original image to access it.'
            : error instanceof Error
              ? error.message
              : 'Could not transfer this image.',
        );
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="niuu-image-preview">
      <div className="niuu-image-preview-toolbar" aria-label="Image preview controls">
        <div className="niuu-image-preview-zoom">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={view.zoom <= MIN_ZOOM}
            onClick={() => zoom(1 / ZOOM_STEP)}
          >
            <ZoomOut size={18} />
          </button>
          <button type="button" aria-label="Fit image" onClick={() => setView(FIT)}>
            <Maximize2 size={16} />
            <span>{Math.round(view.zoom * 100)}%</span>
          </button>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={view.zoom >= MAX_ZOOM}
            onClick={() => zoom(ZOOM_STEP)}
          >
            <ZoomIn size={18} />
          </button>
        </div>
        <div className="niuu-image-preview-actions">
          <button
            type="button"
            disabled={busy || imageFailed || transferDisabled}
            onClick={() => void transfer('copy')}
          >
            <Copy size={16} />
            Copy image
          </button>
          <button
            type="button"
            disabled={busy || transferDisabled}
            onClick={() => void transfer('download')}
          >
            <Download size={16} />
            Download
          </button>
          {!transferDisabled && (
            <a
              href={originalUrl ?? src}
              target="_blank"
              rel="noreferrer"
              aria-label="Open original image"
            >
              <ExternalLink size={16} />
              Open in new tab
            </a>
          )}
        </div>
      </div>
      <div
        className="niuu-image-preview-canvas"
        ref={canvas}
        tabIndex={0}
        role="region"
        aria-label="Image canvas. Drag to pan, scroll to zoom. Arrow keys pan; plus and minus zoom; zero fits."
        onDoubleClick={() => setView(FIT)}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.currentTarget.focus();
          pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={move}
        onPointerUp={(event) => {
          pointers.current.delete(event.pointerId);
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onPointerCancel={(event) => pointers.current.delete(event.pointerId)}
        onLostPointerCapture={(event) => pointers.current.delete(event.pointerId)}
        onKeyDown={(event) => {
          const deltas: Record<string, [number, number]> = {
            ArrowLeft: [40, 0],
            ArrowRight: [-40, 0],
            ArrowUp: [0, 40],
            ArrowDown: [0, -40],
          };
          const delta = deltas[event.key];
          if (delta) {
            event.preventDefault();
            setView((v) => ({ ...v, x: v.x + delta[0], y: v.y + delta[1] }));
          } else if (['+', '=', '-', '0', 'Home'].includes(event.key)) {
            event.preventDefault();
            if (event.key === '0' || event.key === 'Home') setView(FIT);
            else zoom(event.key === '-' ? 1 / ZOOM_STEP : ZOOM_STEP);
          }
        }}
      >
        <svg
          role="img"
          aria-label={name}
          viewBox={`${-view.x / view.zoom} ${-view.y / view.zoom} ${size.width / view.zoom} ${size.height / view.zoom}`}
        >
          <image
            href={src}
            width={size.width}
            height={size.height}
            preserveAspectRatio="xMidYMid meet"
            ref={imageElement}
          />
        </svg>
        {imageFailed && (
          <p role="alert">This image could not be loaded. Try opening the original image.</p>
        )}
      </div>
      <div className="niuu-image-preview-hint">
        Drag to pan · Scroll or pinch to zoom · Double-click to fit
      </div>
      {busy && (
        <p role="status" className="niuu-image-preview-feedback">
          Preparing image…
        </p>
      )}
      {status && (
        <p role="status" className="niuu-image-preview-feedback">
          {status}
        </p>
      )}
      {error && (
        <p role="alert" className="niuu-image-preview-feedback">
          {error}
        </p>
      )}
    </div>
  );
}

export function zoomView(view: View, factor: number, x: number, y: number): View {
  const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.zoom * factor));
  return {
    zoom,
    x: x - ((x - view.x) * zoom) / view.zoom,
    y: y - ((y - view.y) * zoom) / view.zoom,
  };
}
