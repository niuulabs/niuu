import { describe, expect, it } from 'vitest';
import {
  forgeSessionAction,
  groupContentBlocks,
  isForgeSessionTool,
  isStandaloneCardTool,
  type ContentBlock,
} from './groupContentBlocks';

const use = (id: string, name: string): ContentBlock => ({
  type: 'tool_use',
  id,
  name,
  input: {},
});
const done = (id: string): ContentBlock => ({
  type: 'tool_result',
  tool_use_id: id,
  content: 'ok',
});

describe('forgeSessionAction', () => {
  it.each([
    ['mcp__forge__create_session', 'create_session'],
    ['forge.start_session', 'start_session'],
    ['MCP__FORGE__STOP_SESSION', 'stop_session'],
    ['forge__send_message', 'send_message'],
    ['forge:send_message', 'send_message'],
    ['forge/create_session', 'create_session'],
  ])('reads %s', (name, action) => {
    expect(forgeSessionAction(name)).toBe(action);
    expect(isForgeSessionTool(name)).toBe(true);
    expect(isStandaloneCardTool(name)).toBe(true);
  });

  it.each([
    'mcp__forge__list_sessions',
    'mcp__forge__get_session',
    'mcp__forge__session_transcript',
    'mcp__forge__message_status',
    'send_message',
    'SendMessage',
    'mcp__other__create_session',
    'Bash',
  ])('does not claim %s', (name) => {
    expect(forgeSessionAction(name)).toBeNull();
    expect(isStandaloneCardTool(name)).toBe(false);
  });
});

describe('groupContentBlocks with Forge session calls', () => {
  it('keeps every create_session call its own card instead of one group', () => {
    const grouped = groupContentBlocks(
      [
        use('a', 'mcp__forge__create_session'),
        use('b', 'mcp__forge__create_session'),
        done('a'),
        done('b'),
      ],
      true,
    );
    expect(grouped.map((item) => item.kind)).toEqual(['single', 'single']);
  });

  it('does not join a session call to the tool burst around it', () => {
    const grouped = groupContentBlocks(
      [
        use('a', 'Read'),
        use('b', 'forge.send_message'),
        use('c', 'Read'),
        use('d', 'Read'),
        done('a'),
        done('b'),
        done('c'),
        done('d'),
      ],
      true,
    );
    expect(grouped.map((item) => item.kind)).toEqual(['single', 'single', 'group']);
  });

  it('leaves other Forge tools in the ordinary tool group', () => {
    const grouped = groupContentBlocks(
      [use('a', 'mcp__forge__list_sessions'), use('b', 'mcp__forge__get_session')],
      true,
    );
    expect(grouped.map((item) => item.kind)).toEqual(['group']);
  });
});
