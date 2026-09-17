import { describe, expect, it } from 'vitest';
import type { ChatMessagePart } from '@niuulabs/ui';
import { repairCanonicalText } from './canonicalTextRepair';

describe('canonical native text repair', () => {
  const text = (id: string, value: string, complete = false): ChatMessagePart => ({
    type: 'text',
    id,
    text: value,
    turn_id: 'turn',
    thread_id: 'thread',
    complete,
  });
  const tool: ChatMessagePart = {
    type: 'tool_use',
    id: 'call',
    name: 'Bash',
    input: { command: 'pwd' },
  };
  const result: ChatMessagePart = {
    type: 'tool_result',
    tool_use_id: 'call',
    content: 'workspace',
  };
  const target = { item_id: 'a', turn_id: 'turn', thread_id: 'thread' };

  it('inserts a missed leading item before its tool and preserves newer live text and tail', () => {
    const authoritative = text('a', 'café 東京\n\n```sh\npwd\n```', true);
    const newer = text('b', 'New text received during the history GET');
    const tail = text('c', 'New tail');
    const current = [tool, result, newer, tail];
    const repaired = repairCanonicalText(
      [authoritative, tool, result, text('b', 'New')],
      current,
      target,
    )!;
    expect(repaired).toEqual([authoritative, tool, result, newer, tail]);
    expect(repaired[3]).toBe(newer);
    expect(current).toEqual([tool, result, newer, tail]);
    expect(repairCanonicalText([authoritative, tool, result], repaired, target)).toEqual(repaired);
  });

  it('inserts the target between existing text and tools in actual canonical order', () => {
    const before = text('before', 'Before', true);
    const missing = text('a', 'Between', true);
    expect(
      repairCanonicalText([before, tool, missing, result], [before, tool, result], target),
    ).toEqual([before, tool, missing, result]);
  });

  it('repairs partial target bytes but preserves a newer completed target after a suspended GET', () => {
    const old = text('a', 'old authoritative snapshot', true);
    const partial = text('a', 'suffix');
    const fresh = text('a', 'new authoritative completion', true);
    expect(repairCanonicalText([old], [partial], target)).toEqual([old]);
    expect(repairCanonicalText([old], [fresh], target)?.[0]).toBe(fresh);
  });

  it('keeps newer tool inputs/results and live interior parts once', () => {
    const full = text('a', 'A', true);
    const liveTool = { ...tool, input: { command: 'pwd', description: 'Working directory' } };
    const liveResult = { ...result, content: 'workspace\ncomplete' };
    const interior = text('interior', 'Later observation');
    const after = text('after', 'After', true);
    const repaired = repairCanonicalText(
      [full, tool, result, after],
      [liveTool, interior, liveResult, after],
      target,
    );
    expect(repaired).toEqual([full, liveTool, interior, liveResult, after]);
    expect(repaired?.filter((p) => p.type === 'tool_use')).toHaveLength(1);
    expect(repaired?.filter((p) => p.type === 'tool_result')).toHaveLength(1);
  });

  it('does not confuse reused IDs in explicitly different native turns or threads', () => {
    const foreign = { ...text('a', 'foreign', true), turn_id: 'other' };
    const foreignThread = { ...text('a', 'foreign thread', true), thread_id: 'other' };
    expect(repairCanonicalText([foreign], [], target)).toBeNull();
    expect(repairCanonicalText([foreignThread], [], target)).toBeNull();
    const full = text('a', 'A', true);
    expect(repairCanonicalText([foreign, full], [foreign], target)).toEqual([foreign, full]);
  });

  it('fails closed for missing, ambiguous or non-authoritative targets', () => {
    expect(repairCanonicalText([tool], [tool], target)).toBeNull();
    expect(repairCanonicalText([text('a', 'partial')], [], target)).toBeNull();
    expect(
      repairCanonicalText([text('a', 'A', true), text('a', 'A', true)], [], target),
    ).toBeNull();
    expect(repairCanonicalText([{ type: 'text', id: 'a', complete: true }], [], target)).toBeNull();
  });

  it('preserves repeated unidentified legacy occurrences without inventing identities', () => {
    const legacy: ChatMessagePart = { type: 'text', text: 'Repeated prose' };
    const full = text('a', 'A', true);
    expect(repairCanonicalText([legacy, full, legacy], [legacy, legacy], target)).toEqual([
      legacy,
      full,
      legacy,
    ]);
  });
});
