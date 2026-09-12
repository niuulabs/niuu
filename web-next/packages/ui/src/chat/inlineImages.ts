import type { AttachmentMeta } from './types';

const IMAGE_DATA_URI_RE = /data:image\/[a-zA-Z+]+;base64,[A-Za-z0-9+/=]+/g;
// Bare base64 image blobs (no data: prefix), detected by image magic bytes.
const BARE_IMAGE_B64_RE = /(?:\/9j\/|iVBORw0KGgo|R0lGOD[lh]|UklGR)[A-Za-z0-9+/=]{200,}/g;

function inferImageMime(b64: string): string {
  if (b64.startsWith('/9j/')) return 'image/jpeg';
  if (b64.startsWith('iVBORw0KGgo')) return 'image/png';
  if (b64.startsWith('R0lGOD')) return 'image/gif';
  if (b64.startsWith('UklGR')) return 'image/webp';
  return 'image/png';
}

/**
 * Lift inline base64 images out of a message's text `content` into attachment
 * metadata (with a data-URI previewUrl), returning the cleaned text. Server
 * history turns (transformTurns) and older persisted messages can carry a
 * base64 image inside `content`; without this it renders as a giant base64
 * string instead of a small image. Handles both `data:image/...;base64,...`
 * URIs and bare base64 blobs (image magic bytes).
 */
export function extractInlineImages(content: string): {
  text: string;
  attachments: AttachmentMeta[];
} {
  if (!content) return { text: content, attachments: [] };
  // Case 1: content is a JSON-stringified array of Anthropic content blocks —
  // how a user message WITH an image is actually stored on the turn, e.g.
  // [{type:"text",text:"…"},{type:"image",source:{type:"base64",media_type,data}}].
  // Parse text blocks -> message text, image blocks -> attachments with a
  // data-URI preview.
  const trimmed = content.trim();
  if (trimmed.startsWith('[') && trimmed.includes('"type"')) {
    try {
      const blocks: unknown = JSON.parse(trimmed);
      if (Array.isArray(blocks)) {
        const texts: string[] = [];
        const blockAtts: AttachmentMeta[] = [];
        for (const b of blocks) {
          if (!b || typeof b !== 'object') return { text: content, attachments: [] };
          const block = b as { type?: unknown; text?: unknown; source?: unknown };
          if (block.type === 'text' && typeof block.text === 'string') {
            texts.push(block.text);
          } else if (block.type === 'image') {
            const src = (block.source ?? {}) as { media_type?: unknown; data?: unknown };
            const data = typeof src.data === 'string' ? src.data : '';
            if (
              !data ||
              !['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(
                String(src.media_type),
              )
            )
              return { text: content, attachments: [] };
            if (data) {
              const mime = typeof src.media_type === 'string' ? src.media_type : 'image/png';
              blockAtts.push({
                name: 'image',
                type: 'image',
                size: Math.floor((data.length * 3) / 4),
                contentType: mime,
                previewUrl: `data:${mime};base64,${data}`,
              });
            }
          } else {
            return { text: content, attachments: [] };
          }
        }
        if (blockAtts.length > 0)
          return { text: texts.join('\n\n').trim(), attachments: blockAtts };
      }
    } catch {
      /* not a content-block array — fall through to the plain-text scan */
    }
  }
  // Case 2: plain text with an embedded data: URI or bare base64 blob.
  if (content.length < 200) return { text: content, attachments: [] };
  const attachments: AttachmentMeta[] = [];
  const add = (mime: string, dataUri: string, b64Len: number) => {
    attachments.push({
      name: 'image',
      type: 'image',
      size: Math.floor((b64Len * 3) / 4),
      contentType: mime,
      previewUrl: dataUri,
    });
  };
  const textSegments = content.split(/(```[\s\S]*?```|`[^`\n]+`)/g);
  let text = textSegments
    .map((segment, index) => {
      if (index % 2 === 1) return segment;
      let text = segment.replace(IMAGE_DATA_URI_RE, (uri) => {
        const mime = uri.slice(5, uri.indexOf(';')) || 'image/png';
        const b64 = uri.slice(uri.indexOf(',') + 1);
        add(mime, uri, b64.length);
        return '';
      });
      text = text.replace(BARE_IMAGE_B64_RE, (b64) => {
        const mime = inferImageMime(b64);
        add(mime, `data:${mime};base64,${b64}`, b64.length);
        return '';
      });
      return text;
    })
    .join('');
  if (!attachments.length) return { text: content, attachments: [] };
  // drop any now-empty markdown image wrapper left behind, e.g. ![alt]()
  text = text.replace(/!\[[^[\]]*\]\(\s*\)/g, '').trim();
  return { text, attachments };
}
