import { describe, expect, it } from 'vitest';
import {
  validateTextReceipt,
  foldPublicText,
  publicTextContent,
  upsertToolPart,
} from './orderedPublicText';
import type { ChatMessagePart } from '@niuulabs/ui';

describe('identified public text', () => {
  const start = (id: string, phase = 'commentary') => ({
    type: 'content_block_start',
    item_id: id,
    turn_id: 'turn',
    thread_id: 'thread',
    content_block: { type: 'text', id, phase },
  });
  const delta = (id: string, text: string) => ({
    type: 'content_block_delta',
    item_id: id,
    turn_id: 'turn',
    thread_id: 'thread',
    delta: { type: 'text_delta', text },
  });
  const complete = (id: string, text: string) => ({
    type: 'assistant',
    item_id: id,
    turn_id: 'turn',
    thread_id: 'thread',
    message: { content: [{ type: 'text', id, text }] },
  });

  it('retains exact bytes for every possible two-chunk split, with authoritative completion once', () => {
    const text = 'café 東京\n\n```sh\nprintf "yes"\n```';
    for (let at = 0; at <= text.length; at++) {
      let parts = foldPublicText([], start('a'))!;
      parts = foldPublicText(parts, delta('a', text.slice(0, at)))!;
      parts = foldPublicText(parts, delta('a', text.slice(at)))!;
      parts = foldPublicText(parts, complete('a', text))!;
      parts = foldPublicText(parts, complete('a', text))!;
      expect(parts).toHaveLength(1);
      expect(parts[0]).toMatchObject({ id: 'a', text, phase: 'commentary', complete: true });
      expect(foldPublicText(parts, delta('a', 'late'))).toEqual(parts);
    }
  });

  it('keeps adjacent native messages distinct and completion-only text visible', () => {
    let parts = foldPublicText([], complete('a', 'First.'))!;
    parts = foldPublicText(parts, start('b', 'final_answer'))!;
    parts = foldPublicText(parts, complete('b', 'Answer.'))!;
    expect(parts.map((part) => part.id)).toEqual(['a', 'b']);
    expect(publicTextContent(parts)).toBe('First.\n\nAnswer.');
    expect(parts[1]?.phase).toBe('final_answer');
  });

  it('refreshes tools and text at their original anchors through a serialized snapshot', () => {
    let parts = foldPublicText([], complete('a', 'Before.'))!;
    parts = upsertToolPart(parts, { type: 'tool_use', id: 'tool', name: 'Bash', input: {} });
    parts = foldPublicText(parts, complete('b', 'After.'))!;
    parts = JSON.parse(JSON.stringify(parts)) as ChatMessagePart[];
    parts = upsertToolPart(parts, {
      type: 'tool_use',
      id: 'tool',
      name: 'Bash',
      input: { command: 'ls' },
    });
    parts = foldPublicText(parts, complete('a', 'Before, complete.'))!;
    expect(parts.map((part) => part.id)).toEqual(['a', 'tool', 'b']);
    expect(parts[0]?.text).toBe('Before, complete.');
  });

  it('keeps unknown phases and declines unkeyed legacy frames', () => {
    expect(foldPublicText([], start('x', 'future_public_phase'))?.[0]?.phase).toBe(
      'future_public_phase',
    );
    expect(
      foldPublicText([], {
        type: 'content_block_delta',
        delta: { type: 'text_delta', text: 'legacy' },
      }),
    ).toBeNull();
    expect(foldPublicText([], { type: 'content_block_stop', item_id: 'x' })).toBeNull();
  });
});

it('a verified large-text stop seals the same anchor without replacing streamed bytes', () => {
  const parts = foldPublicText([], {
    type: 'content_block_delta',
    item_id: 'large',
    turn_id: 't',
    delta: { type: 'text_delta', text: 'café 東京\n\n```code```' },
  })!;
  const stopped = foldPublicText(parts, {
    type: 'content_block_stop',
    item_id: 'large',
    turn_id: 't',
    phase: 'final_answer',
    complete: true,
  })!;
  expect(stopped).toHaveLength(1);
  expect(stopped[0]).toMatchObject({
    id: 'large',
    text: parts[0]?.text,
    phase: 'final_answer',
    complete: true,
  });
  expect(
    foldPublicText(stopped, {
      type: 'content_block_delta',
      item_id: 'large',
      turn_id: 't',
      delta: { type: 'text_delta', text: 'late' },
    }),
  ).toEqual(stopped);
});

it('validates optimized completion bytes and SHA without guessing missing content', async () => {
  const digest = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';
  expect(
    await validateTextReceipt('abc', {
      type: 'content_block_stop',
      text_bytes: 3,
      text_sha256: digest,
    }),
  ).toBe(true);
  expect(
    await validateTextReceipt('ab', {
      type: 'content_block_stop',
      text_bytes: 3,
      text_sha256: digest,
    }),
  ).toBe(false);
  expect(
    await validateTextReceipt('abd', {
      type: 'content_block_stop',
      text_bytes: 3,
      text_sha256: digest,
    }),
  ).toBe(false);
  expect(
    await validateTextReceipt('café 東京', { type: 'content_block_stop', text_bytes: 12 }),
  ).toBe(true);
});

it('traverses an identified mixed assistant message in wire order without flattening its tools', () => {
  const event = {
    type: 'assistant',
    turn_id: 'turn',
    message: {
      content: [
        { type: 'text', id: 'a', text: 'Before', phase: 'commentary' },
        { type: 'tool_use', id: 'tool', name: 'Bash', input: { command: 'pwd' } },
        { type: 'text', id: 'b', text: 'After', phase: 'final_answer' },
        { type: 'tool_result', tool_use_id: 'tool', content: 'workspace' },
      ],
    },
  };
  const parts = foldPublicText([], event)!;
  expect(parts.map((part) => part.id ?? part.tool_use_id)).toEqual(['a', 'tool', 'b', 'tool']);
  expect(publicTextContent(parts)).toBe('Before\n\nAfter');
  expect(foldPublicText(parts, event)).toEqual(parts);
});
