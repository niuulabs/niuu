import { describe, expect, it } from 'vitest';
import type { ChatMessage, ChatMessagePart } from '../types';
import { reconcileHistoryMessages } from './historyReconciliation';

const text = (id: string, value: string, complete = true): ChatMessagePart => ({
  type: 'text',
  id,
  text: value,
  turn_id: 'native',
  thread_id: 'thread',
  complete,
});
const message = (id: string, parts?: ChatMessagePart[]): ChatMessage => ({
  id,
  role: 'assistant',
  content: id,
  createdAt: new Date(0),
  parts,
  status: 'running',
});
const tool: ChatMessagePart = {
  type: 'tool_use',
  id: 'tool',
  name: 'Read',
  input: { path: 'file' },
};

describe('REST and live history reconciliation', () => {
  it('restores earlier commentary and tools, retaining a newer completion and result once', () => {
    const saved = message('saved', [text('a', 'Before'), tool, text('b', 'Old')]);
    const result: ChatMessagePart = { type: 'tool_result', tool_use_id: 'tool', content: 'Read' };
    const live = message('live', [tool, result, text('b', 'Latest'), text('c', 'After')]);
    const merged = reconcileHistoryMessages([saved], [live]);
    expect(merged.recent[0]?.parts).toEqual([
      text('a', 'Before'),
      tool,
      result,
      text('b', 'Latest'),
      text('c', 'After'),
    ]);
    expect(merged.recent[0]?.content).toBe('Before\n\nLatest\n\nAfter');
    expect(merged.recent[0]?.id).toBe('live');
  });
  it('repairs a completed item seen only as a suffix, without reviving a finished turn', () => {
    const live = { ...message('live', [text('a', 'suffix', false)]), status: 'done' as const };
    const { recent } = reconcileHistoryMessages(
      [message('saved', [text('a', 'prefix suffix')])],
      [live],
    );
    expect(recent[0]?.content).toBe('prefix suffix');
    expect(recent[0]?.status).toBe('done');
  });
  it('does not bind distinct native turns, threads or authors just because an item ID is reused', () => {
    const saved = message('saved', [text('a', 'Saved')]);
    const others = [
      message('other-turn', [{ ...text('a', 'Other'), turn_id: 'other' }]),
      message('other-thread', [{ ...text('a', 'Other'), thread_id: 'other' }]),
      { ...message('user', [text('a', 'Other')]), role: 'user' as const },
    ];
    expect(reconcileHistoryMessages([saved], others)).toEqual({ recent: [saved], current: others });
  });
  it('retains unidentified legacy history and previews instead of silently dropping them', () => {
    const legacy = message('legacy');
    const preview = { ...message('preview', [text('a', 'Saved')]), historyPreview: true };
    const live = message('live', [text('b', 'New')]);
    expect(reconcileHistoryMessages([legacy, preview], [live])).toEqual({
      recent: [legacy, preview],
      current: [live],
    });
  });
  it('uses tool identity on retained peers without native IDs and joins observed fragments once', () => {
    const saved = message('saved', [tool]);
    const fragments = [message('first', [tool]), message('last', [tool, text('a', 'After')])];
    const { recent, current } = reconcileHistoryMessages([saved], fragments);
    expect(recent[0]?.parts).toEqual([tool, text('a', 'After')]);
    expect(recent[0]?.id).toBe('last');
    expect(current.map((row) => row.id)).toEqual(['last']);
  });
  it('preserves observed text and metadata for same-row legacy messages without parts', () => {
    const live = { ...message('same'), content: 'Latest', metadata: { messageType: 'assistant' } };
    expect(reconcileHistoryMessages([message('same')], [live]).recent[0]).toEqual(live);
    expect(
      reconcileHistoryMessages([message('same')], [{ ...live, content: '' }]).recent[0]?.content,
    ).toBe('same');
  });
});
