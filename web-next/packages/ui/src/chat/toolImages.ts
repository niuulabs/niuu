import type { ToolResultBlock, ToolUseBlock } from './components/ToolBlock/groupContentBlocks';

export interface ToolImage {
  toolUseId: string;
  index: number;
  name: string;
  path?: string;
  mime?: string;
  width?: number;
  height?: number;
  /** Full content already delivered on the wire; never expanded merely to identify an image. */
  content?: unknown;
  indexedPreview?: boolean;
}

type RecordValue = Record<string, unknown>;
function object(value: unknown): RecordValue | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as RecordValue)
    : undefined;
}
const positive = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined;
const mimeType = (value: unknown) =>
  typeof value === 'string' && value.startsWith('image/') ? value : undefined;

export interface ImagePayload {
  data?: string;
  mime?: string;
  width?: number;
  height?: number;
}

/** Read only typed image envelopes, never filenames mentioned in command output or source code. */
export function imagePayloads(content: unknown): ImagePayload[] {
  if (typeof content === 'string') {
    const start = content.trimStart();
    if (!start.startsWith('{') && !start.startsWith('[')) return [];
    // Normal text reads can be megabytes; only parse strings carrying an early image discriminator.
    if (
      !start.startsWith('[') &&
      !/(?:"type"\s*:\s*"(?:image|input_image|output_image|image_url|Image)"|"base64"\s*:)/.test(
        start.slice(0, 512),
      )
    )
      return [];
    try {
      content = JSON.parse(content);
    } catch {
      return [];
    }
  }
  const root = object(content);
  const file = object(root?.file);
  if (root?.type === 'image' && file) {
    const dimensions = object(file.dimensions);
    return [
      {
        data: typeof file.base64 === 'string' ? file.base64 : undefined,
        mime: mimeType(file.type),
        width: positive(dimensions?.displayWidth),
        height: positive(dimensions?.displayHeight),
      },
    ];
  }
  const images: ImagePayload[] = [];
  for (const item of Array.isArray(content) ? content : [content]) {
    const block = object(item);
    if (!block) continue;
    if (block.type === 'image') {
      const source = object(block.source);
      const data = source?.data ?? block.data;
      if (source || typeof data === 'string') {
        images.push({
          data: typeof data === 'string' ? data : undefined,
          mime: mimeType(source?.media_type ?? block.mimeType),
        });
        continue;
      }
    }
    if (
      !['image', 'input_image', 'output_image', 'image_url', 'Image'].includes(String(block.type))
    )
      continue;
    const raw = block.image_url ?? block.imageUrl;
    const url = typeof raw === 'string' ? raw : object(raw)?.url;
    if (typeof url !== 'string') continue;
    const match = /^data:(image\/[\w.+-]+);base64,([\s\S]+)$/.exec(url);
    if (match) images.push({ mime: match[1], data: match[2] });
  }
  return images;
}

const parsed = new WeakMap<ToolResultBlock, ImagePayload[]>();
export function toolImages(result?: ToolResultBlock, call?: ToolUseBlock): ToolImage[] {
  if (!result || result.is_error || !result.tool_use_id) return [];
  let metadata = parsed.get(result);
  if (!metadata) {
    metadata = imagePayloads(result.content).map(({ mime, width, height }) => ({
      mime,
      width,
      height,
    }));
    if (metadata.length === 0 && result.is_image) {
      metadata = result.image_previews?.length
        ? result.image_previews.map((item) => ({
            mime: mimeType(item.mime_type),
            width: positive(item.img_w),
            height: positive(item.img_h),
          }))
        : [
            {
              mime: mimeType(result.mime_type),
              width: positive(result.img_w),
              height: positive(result.img_h),
            },
          ];
    }
    if (metadata.length === 0 && result.truncated) {
      // Retained pre-hint Claude history: require the envelope prefix AND known base64 magic.
      const match =
        /^\s*\{\s*"(?:type"\s*:\s*"image"\s*,\s*"file|file)"\s*:\s*\{\s*"base64"\s*:\s*"(iVBOR|\/9j\/|R0lGOD|UklGR)/.exec(
          result.preview ?? '',
        );
      if (match)
        metadata = [
          {
            mime: (
              {
                iVBOR: 'image/png',
                '/9j/': 'image/jpeg',
                R0lGOD: 'image/gif',
                UklGR: 'image/webp',
              } as Record<string, string>
            )[match[1]!],
          },
        ];
    }
    parsed.set(result, metadata);
  }
  const rawPath = call?.input.file_path ?? call?.input.path;
  const path = typeof rawPath === 'string' ? rawPath : undefined;
  const basename = path?.split(/[\\/]/).pop();
  return metadata.map((meta, index) => ({
    toolUseId: result.tool_use_id,
    index,
    name: basename
      ? `${basename}${metadata.length > 1 ? ` (${index + 1})` : ''}`
      : `Tool image${metadata.length > 1 ? ` ${index + 1}` : ''}`,
    path,
    mime: meta.mime,
    width: meta.width,
    height: meta.height,
    content: result.content,
    indexedPreview: Boolean(result.image_previews?.length),
  }));
}
