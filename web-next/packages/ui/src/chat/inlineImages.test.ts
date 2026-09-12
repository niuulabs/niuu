import { describe, it, expect } from 'vitest';
import { extractInlineImages } from './inlineImages';
const data = 'iVBORw0KGgo' + 'A'.repeat(220);
const image = { type: 'image', source: { type: 'base64', media_type: 'image/png', data } };
describe('history images', () => {
  it('restores structured images with text', () => {
    const result = extractInlineImages(
      JSON.stringify([{ type: 'text', text: 'See screenshot' }, image]),
    );
    expect(result.text).toBe('See screenshot');
    expect(result.attachments[0]?.previewUrl).toBe(`data:image/png;base64,${data}`);
  });
  it('preserves unknown blocks and invalid image content', () => {
    for (const content of [
      JSON.stringify([image, { type: 'tool_result', content: 'keep' }]),
      JSON.stringify([image, null]),
      JSON.stringify([{ type: 'image', source: { data, media_type: 'text/html' } }]),
    ]) {
      expect(extractInlineImages(content)).toEqual({ text: content, attachments: [] });
    }
  });
  it('extracts inline and bare images but preserves code examples', () => {
    expect(extractInlineImages(`Screenshot ![screen](data:image/png;base64,${data})`).text).toBe(
      'Screenshot',
    );
    expect(extractInlineImages(data).attachments).toHaveLength(1);
    const code = '```text\n' + data + '\n```';
    expect(extractInlineImages(code)).toEqual({ text: code, attachments: [] });
  });
  it('leaves short text and malformed JSON untouched', () => {
    for (const text of ['', 'hello', '[{"type": broken'])
      expect(extractInlineImages(text)).toEqual({ text, attachments: [] });
  });
  it('preserves repeated unfinished image labels without rescanning each suffix', () => {
    const labels = '!['.repeat(100_000);
    const result = extractInlineImages(`${labels}\n![screen](data:image/png;base64,${data})`);
    expect(result.text).toBe(labels);
    expect(result.attachments).toHaveLength(1);
  });
  it('preserves whitespace when a long message has no images', () => {
    const text = '  ' + 'Ordinary text '.repeat(30) + '\n';
    expect(extractInlineImages(text)).toEqual({ text, attachments: [] });
  });
  it('recognizes JPEG, GIF and WebP histories', () => {
    for (const [prefix, mime] of [
      ['/9j/', 'image/jpeg'],
      ['R0lGODlh', 'image/gif'],
      ['UklGR', 'image/webp'],
    ]) {
      expect(extractInlineImages(prefix + 'A'.repeat(220)).attachments[0]?.contentType).toBe(mime);
    }
  });
});
