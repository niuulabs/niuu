import { describe, expect, it } from 'vitest';
import { imagePayloads, toolImages } from './toolImages';
import {
  groupContentBlocks,
  type ToolResultBlock,
  type ToolUseBlock,
} from './components/ToolBlock/groupContentBlocks';
import { hideToolParts } from './hooks/useRoomState';

const read: ToolUseBlock = {
  type: 'tool_use',
  id: 'read',
  name: 'Read',
  input: { file_path: '/workspace/chart.png' },
};
const result: ToolResultBlock = {
  type: 'tool_result',
  tool_use_id: 'read',
  truncated: true,
  is_image: true,
  mime_type: 'image/png',
  img_w: 640,
  img_h: 480,
};
describe('images returned by tools', () => {
  it('identifies metadata-only Claude results without fetching their bytes', () => {
    expect(toolImages(result, read)).toEqual([
      expect.objectContaining({
        toolUseId: 'read',
        index: 0,
        name: 'chart.png',
        width: 640,
        height: 480,
        mime: 'image/png',
      }),
    ]);
    expect(toolImages({ ...result, is_error: true }, read)).toEqual([]);
  });
  it('recognizes full Read envelopes and mixed Anthropic, MCP and Codex blocks', () => {
    const envelope = {
      type: 'image',
      file: {
        base64: 'AAAA',
        type: 'image/png',
        dimensions: { displayWidth: 20, displayHeight: 40 },
      },
    };
    expect(imagePayloads(JSON.stringify(envelope))).toEqual([
      { data: 'AAAA', mime: 'image/png', width: 20, height: 40 },
    ]);
    const blocks = [
      { type: 'text', text: 'Rendered images' },
      { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: 'AAAA' } },
      { type: 'image', mimeType: 'image/png', data: 'BBBB' },
      { type: 'input_image', image_url: 'data:image/png;base64,CCCC' },
      { type: 'image_url', image_url: { url: 'data:image/webp;base64,DDDD' } },
      { type: 'Image', imageUrl: 'data:image/png;base64,EEEE' },
    ];
    expect(imagePayloads(blocks).map((image) => image.data)).toEqual([
      'AAAA',
      'BBBB',
      'CCCC',
      'DDDD',
      'EEEE',
    ]);
  });
  it.each(['iVBOR', '/9j/', 'R0lGOD', 'UklGR'])(
    'recognizes a retained image envelope from its %s magic prefix',
    (magic) => {
      expect(
        toolImages({
          type: 'tool_result',
          tool_use_id: 'old',
          truncated: true,
          preview: `{"file": {"base64": "${magic}…`,
        }),
      ).toHaveLength(1);
    },
  );
  it.each([
    'cat chart.png',
    '{"file":{"content":"image.png"}}',
    '{"type":"text","file":{"content":"base64 code"}}',
    '[{"type":"text","text":"data:image/png;base64,AAAA"}]',
    '{broken',
  ])('does not invent image events from text: %s', (content) => {
    expect(toolImages({ type: 'tool_result', tool_use_id: 'text', content })).toEqual([]);
  });
  it('keeps multiple images in stream order outside tool groups and deduplicates a replayed result', () => {
    const multi = {
      ...result,
      image_previews: [
        { index: 0, mime_type: 'image/png' },
        { index: 1, mime_type: 'image/jpeg' },
      ],
    };
    const shell: ToolUseBlock = {
      type: 'tool_use',
      id: 'shell',
      name: 'Bash',
      input: { command: 'pwd' },
    };
    const groups = groupContentBlocks(
      [
        { type: 'text', text: 'Before' },
        shell,
        read,
        multi,
        multi,
        { ...shell, id: 'next' },
        { type: 'text', text: 'After' },
      ],
      true,
    );
    expect(groups.map((group) => group.kind)).toEqual([
      'text',
      'single',
      'image',
      'image',
      'single',
      'text',
    ]);
    expect(
      groups.filter((group) => group.kind === 'image').map((group) => group.image.index),
    ).toEqual([0, 1]);
    expect(groupContentBlocks([result], true)[0]?.kind).toBe('image');
  });
  it('preserves images when tools are hidden while keeping other tool boundaries', () => {
    const parts = [
      read,
      result,
      { ...read, id: 'text', input: { file_path: '/workspace/README.md' } },
      { type: 'tool_result' as const, tool_use_id: 'text', content: 'Text file' },
    ];
    const hidden = hideToolParts(parts);
    expect(hidden).toEqual([read, result, { type: 'tool_separator', id: 'text' }]);
    expect(groupContentBlocks(hidden, true).map((item) => item.kind)).toEqual([
      'image',
      'separator',
    ]);
  });
});
