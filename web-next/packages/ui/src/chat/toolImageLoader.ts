import { getAuthHeaders } from '@niuulabs/query';
import { conversationUrl } from './hooks/historyPaging';
import { imagePayloads, type ToolImage } from './toolImages';

const PREVIEW_CONCURRENCY = 3;
const PREVIEW_CACHE_ENTRIES = 48;
const PREVIEW_BYTE_LIMIT = 2 * 1024 * 1024;
const FULL_BYTE_LIMIT = 64 * 1024 * 1024;
const RASTER_MIME = /^image\/(?:png|jpeg|gif|webp|avif|bmp|x-icon)$/;

export function toolResultUrl(endpoint: string, toolId: string, preview = false, index = 0): URL {
  const url = conversationUrl(endpoint);
  const suffix = `tool-result/${encodeURIComponent(toolId)}${preview ? '/preview' : ''}`;
  url.pathname = url.pathname.endsWith('/api/conversation/history')
    ? url.pathname.replace(/history$/, suffix)
    : url.pathname.replace(/conversation$/, suffix);
  if (preview && index > 0) url.searchParams.set('image_index', String(index));
  return url;
}

async function boundedBytes(response: Response, limit: number): Promise<Uint8Array<ArrayBuffer>> {
  if (Number(response.headers.get('content-length')) > limit) {
    await response.body?.cancel();
    throw new Error('This image exceeds the preview size limit.');
  }
  if (!response.body) {
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length > limit) throw new Error('This image exceeds the preview size limit.');
    return bytes;
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) {
        await reader.cancel();
        throw new Error('This image exceeds the preview size limit.');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

export async function loadToolImage(
  image: ToolImage,
  endpoint: string | null,
  signal: AbortSignal,
): Promise<Blob> {
  let content = image.content;
  if (!imagePayloads(content)[image.index]?.data) {
    if (!endpoint) throw new Error('Image access requires this session’s connection.');
    const response = await fetch(toolResultUrl(endpoint, image.toolUseId), {
      headers: getAuthHeaders(),
      signal,
    });
    if (!response.ok) throw new Error(`Could not load image (HTTP ${response.status}).`);
    const detail = JSON.parse(
      new TextDecoder().decode(await boundedBytes(response, FULL_BYTE_LIMIT)),
    );
    if (detail.tool_use_id !== image.toolUseId || detail.is_error)
      throw new Error('The image result did not match this tool call.');
    content = detail.content;
  }
  const payload = imagePayloads(content)[image.index];
  if (!payload?.data || !payload.mime || !RASTER_MIME.test(payload.mime))
    throw new Error('This tool result does not contain a supported image.');
  if (payload.data.length > FULL_BYTE_LIMIT)
    throw new Error('This image exceeds the preview size limit.');
  // Let the browser decode base64 asynchronously; never inflate image bytes during render.
  const response = await fetch(`data:${payload.mime};base64,${payload.data}`, { signal });
  const bytes = await boundedBytes(response, FULL_BYTE_LIMIT);
  return new Blob([bytes], { type: payload.mime });
}

async function inlineThumbnail(image: ToolImage, signal: AbortSignal): Promise<Blob> {
  const blob = await loadToolImage(image, null, signal);
  if (typeof createImageBitmap !== 'function')
    throw new Error('Thumbnail decoding is unavailable. Open the image to view it.');
  const bitmap = await createImageBitmap(blob);
  try {
    const scale = Math.min(1, 400 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Thumbnail decoding is unavailable. Open the image to view it.');
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (result) => (result ? resolve(result) : reject(new Error('Could not create thumbnail.'))),
        'image/png',
      ),
    );
  } finally {
    bitmap.close();
  }
}

export interface ImageSnapshot {
  status: 'idle' | 'loading' | 'ready' | 'error';
  blob?: Blob;
  error?: string;
}
const IDLE: ImageSnapshot = { status: 'idle' };
type Entry = {
  image: ToolImage;
  snapshot: ImageSnapshot;
  listeners: Set<() => void>;
  controller?: AbortController;
};

/** Session-owned thumbnail queue. Only visible cards subscribe; full images are never prefetched. */
export class ToolImageLoader {
  private entries = new Map<string, Entry>();
  private waiting: Entry[] = [];
  private active = 0;
  constructor(readonly endpoint: string | null) {}
  private key(image: ToolImage) {
    return `${image.toolUseId}:${image.index}`;
  }
  snapshot = (image: ToolImage) => this.entries.get(this.key(image))?.snapshot ?? IDLE;
  subscribe(image: ToolImage, listener: () => void) {
    const key = this.key(image);
    let entry = this.entries.get(key);
    if (!entry) {
      entry = { image, snapshot: IDLE, listeners: new Set() };
      this.entries.set(key, entry);
    }
    entry.image = image;
    entry.listeners.add(listener);
    // Refresh LRU order without dropping subscriptions.
    this.entries.delete(key);
    this.entries.set(key, entry);
    if (entry.snapshot.status === 'idle') this.enqueue(entry);
    return () => {
      entry.listeners.delete(listener);
      if (entry.listeners.size === 0 && entry.snapshot.status === 'loading') {
        this.waiting = this.waiting.filter((item) => item !== entry);
        entry.controller?.abort();
        entry.snapshot = IDLE;
      }
      this.trim();
    };
  }
  retry(image: ToolImage) {
    const entry = this.entries.get(this.key(image));
    if (entry && entry.snapshot.status !== 'loading') this.enqueue(entry);
  }
  dispose() {
    for (const entry of this.entries.values()) entry.controller?.abort();
    this.entries.clear();
    this.waiting = [];
  }
  private notify(entry: Entry) {
    for (const listener of entry.listeners) listener();
  }
  private enqueue(entry: Entry) {
    entry.snapshot = { status: 'loading' };
    this.waiting.push(entry);
    this.notify(entry);
    this.pump();
  }
  private trim() {
    for (const [key, entry] of this.entries) {
      if (this.entries.size <= PREVIEW_CACHE_ENTRIES) break;
      if (entry.listeners.size === 0 && entry.snapshot.status !== 'loading')
        this.entries.delete(key);
    }
  }
  private pump() {
    while (this.active < PREVIEW_CONCURRENCY && this.waiting.length) {
      const entry = this.waiting.shift()!;
      const controller = new AbortController();
      entry.controller = controller;
      this.active++;
      void this.preview(entry.image, controller.signal)
        .then((blob) => {
          if (!controller.signal.aborted) {
            entry.snapshot = { status: 'ready', blob };
            this.notify(entry);
          }
        })
        .catch((failure: unknown) => {
          if (!controller.signal.aborted) {
            entry.snapshot = {
              status: 'error',
              error: failure instanceof Error ? failure.message : 'Could not load thumbnail.',
            };
            this.notify(entry);
          }
        })
        .finally(() => {
          this.active--;
          this.trim();
          this.pump();
        });
    }
  }
  private async preview(image: ToolImage, signal: AbortSignal): Promise<Blob> {
    if (!this.endpoint || (image.index > 0 && !image.indexedPreview))
      return inlineThumbnail(image, signal);
    const response = await fetch(toolResultUrl(this.endpoint, image.toolUseId, true, image.index), {
      headers: getAuthHeaders(),
      signal,
    });
    if (!response.ok)
      throw new Error(`Thumbnail unavailable (HTTP ${response.status}). Open the image or retry.`);
    const mime = response.headers.get('content-type')?.split(';')[0]?.trim() ?? '';
    if (!RASTER_MIME.test(mime)) throw new Error('The server returned an invalid image thumbnail.');
    const bytes = await boundedBytes(response, PREVIEW_BYTE_LIMIT);
    return new Blob([bytes], { type: mime });
  }
}
