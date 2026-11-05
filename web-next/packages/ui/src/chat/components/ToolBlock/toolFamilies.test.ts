import { describe, expect, it } from 'vitest';
import {
  isSubagentTool,
  runFamilies,
  summarizeRun,
  toolDiffstat,
  toolFamily,
  toolFamilyInfo,
  toolFilePaths,
  toolResultText,
  toolSubject,
  toolTicker,
  sumDiffstat,
} from './toolFamilies';
import type { ToolUseBlock } from './groupContentBlocks';

const toolUse = (name: string, input: Record<string, unknown> = {}, id = name): ToolUseBlock => ({
  type: 'tool_use',
  id,
  name,
  input,
});

describe('toolFamily', () => {
  it.each([
    ['Bash', 'command'],
    ['shell', 'command'],
    ['exec_command', 'command'],
    ['exec', 'command'],
    ['container.exec', 'command'],
    ['Edit', 'edit'],
    ['MultiEdit', 'edit'],
    ['NotebookEdit', 'edit'],
    ['Write', 'edit'],
    ['apply_patch', 'edit'],
    ['Read', 'read'],
    ['LS', 'read'],
    ['Glob', 'search'],
    ['Grep', 'search'],
    ['WebFetch', 'web'],
    ['WebSearch', 'web'],
    ['web_search', 'web'],
    ['TodoWrite', 'plan'],
    ['update_plan', 'plan'],
    ['Task', 'agent'],
    ['Agent', 'agent'],
    ['Agent:wait', 'agent'],
    ['AskUserQuestion', 'question'],
    ['request_user_input', 'question'],
    ['mcp__mimir__search', 'other'],
    ['mimir.mimir_search', 'other'],
    ['something_new', 'other'],
  ])('%s is a %s call', (name, family) => {
    expect(toolFamily(name)).toBe(family);
  });

  it('gives each family present and past verbs and an icon', () => {
    const command = toolFamilyInfo('Bash');
    expect(command.present).toBe('Running');
    expect(command.past).toBe('Ran');
    expect(toolFamilyInfo('Edit').present).toBe('Editing');
    expect(toolFamilyInfo('Read').past).toBe('Read');
    for (const name of ['Bash', 'Edit', 'Read', 'Grep', 'WebFetch', 'TodoWrite', 'Task', 'x']) {
      expect(toolFamilyInfo(name).icon).toBeTruthy();
    }
  });

  it('recognises subagent tools by exact name only', () => {
    expect(isSubagentTool('Task')).toBe(true);
    expect(isSubagentTool('agent')).toBe(true);
    expect(isSubagentTool('Agent:wait')).toBe(false);
    expect(isSubagentTool('Read')).toBe(false);
  });
});

describe('toolSubject and toolTicker', () => {
  it('names the command, the file, the pattern and the URL', () => {
    expect(toolSubject(toolUse('Bash', { command: 'pytest tests/\nsecond line' }))).toBe(
      'pytest tests/',
    );
    expect(toolSubject(toolUse('exec_command', { cmd: 'ls -la' }))).toBe('ls -la');
    expect(toolSubject(toolUse('exec', { input: 'text(await tools.list());\nreturn 1;' }))).toBe(
      'text(await tools.list());',
    );
    expect(toolSubject(toolUse('shell', { command: ['git', 'status'] }))).toBe('git status');
    expect(toolSubject(toolUse('Edit', { file_path: 'src/gates.py' }))).toBe('src/gates.py');
    expect(toolSubject(toolUse('Read', { path: 'a.ts' }))).toBe('a.ts');
    expect(toolSubject(toolUse('Grep', { pattern: 'resolve_gate' }))).toBe('resolve_gate');
    expect(toolSubject(toolUse('WebFetch', { url: 'https://example.com' }))).toBe(
      'https://example.com',
    );
    expect(toolSubject(toolUse('WebSearch', { action: { query: 'niuu' } }))).toBe('niuu');
    expect(toolSubject(toolUse('TodoWrite', { todos: [] }))).toBe('');
    expect(toolSubject(toolUse('Task', { description: 'Check', prompt: 'long' }))).toBe('Check');
    expect(
      toolSubject(toolUse('AskUserQuestion', { questions: [{ question: 'Which set?' }] })),
    ).toBe('Which set?');
    expect(toolSubject(toolUse('AskUserQuestion', { question: 'Sure?' }))).toBe('Sure?');
    expect(toolSubject(toolUse('mcp__mimir__search'))).toBe('mcp:mimir__search');
  });

  it('names a Codex multi-file change by its first file and the rest', () => {
    const change = toolUse('Edit', {
      changes: [
        { path: 'a.py', diff: '+x' },
        { path: 'b.py', diff: '+y' },
        { path: 'c.py', diff: '+z' },
      ],
    });
    expect(toolFilePaths(change)).toEqual(['a.py', 'b.py', 'c.py']);
    expect(toolSubject(change)).toBe('a.py and 2 more');
  });

  it('uses the preview of an elided input', () => {
    expect(toolSubject(toolUse('Bash', { _elided_input: true, preview: 'make test\nmore' }))).toBe(
      'make test',
    );
  });

  it('words the live ticker with the present verb', () => {
    expect(toolTicker(toolUse('Bash', { command: 'pytest tests/' }))).toBe('Running pytest tests/');
    expect(toolTicker(toolUse('Edit', { file_path: 'src/gates.py' }))).toBe('Editing src/gates.py');
    expect(toolTicker(toolUse('TodoWrite'))).toBe('Updating plan');
  });
});

describe('toolDiffstat', () => {
  it('counts Edit old/new lines', () => {
    expect(
      toolDiffstat(
        toolUse('Edit', { file_path: 'a', old_string: 'one\ntwo', new_string: 'x\ny\nz\n' }),
      ),
    ).toEqual({ added: 3, removed: 2 });
  });

  it('counts MultiEdit edits together', () => {
    expect(
      toolDiffstat(
        toolUse('MultiEdit', {
          file_path: 'a',
          edits: [
            { old_string: 'a', new_string: 'b\nc' },
            { old_string: 'd\ne', new_string: '' },
            'not an edit',
          ],
        }),
      ),
    ).toEqual({ added: 2, removed: 3 });
  });

  it('counts Write content as additions', () => {
    expect(toolDiffstat(toolUse('Write', { file_path: 'a', content: 'l1\nl2\nl3\n' }))).toEqual({
      added: 3,
      removed: 0,
    });
  });

  it('counts Codex fileChange diffs, skipping the file headers', () => {
    const change = toolUse('Edit', {
      changes: [
        { path: 'a.py', diff: '--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n ctx\n-old\n+new\n+more' },
        { path: 'b.py', diff: '+hello' },
      ],
    });
    expect(toolDiffstat(change)).toEqual({ added: 3, removed: 1 });
  });

  it('counts an apply_patch body when the input carries the patch', () => {
    const patch = [
      '*** Begin Patch',
      '*** Update File: a.py',
      '@@',
      ' ctx',
      '-old',
      '+new',
      '+extra',
      '*** End Patch',
    ].join('\n');
    expect(toolDiffstat(toolUse('apply_patch', { input: patch }))).toEqual({
      added: 2,
      removed: 1,
    });
    expect(toolDiffstat(toolUse('apply_patch', { command: ['apply_patch', patch] }))).toEqual({
      added: 2,
      removed: 1,
    });
  });

  it('reports nothing for other families, elided inputs and edits without text', () => {
    expect(toolDiffstat(toolUse('Bash', { command: '+1' }))).toEqual({ added: 0, removed: 0 });
    expect(toolDiffstat(toolUse('Edit', { _elided_input: true, preview: 'x' }))).toEqual({
      added: 0,
      removed: 0,
    });
    expect(toolDiffstat(toolUse('Edit', { file_path: 'a' }))).toEqual({ added: 0, removed: 0 });
  });

  it('sums a run', () => {
    expect(
      sumDiffstat([
        toolUse('Edit', { old_string: 'a', new_string: 'b' }, '1'),
        toolUse('Write', { content: 'x\ny' }, '2'),
      ]),
    ).toEqual({ added: 3, removed: 1 });
  });
});

describe('summarizeRun', () => {
  it('humanises each family in order of first appearance', () => {
    const run = [
      toolUse('Bash', { command: 'a' }, '1'),
      toolUse('Edit', { file_path: 'x.py' }, '2'),
      toolUse('Bash', { command: 'b' }, '3'),
      toolUse('Read', { file_path: 'a' }, '4'),
      toolUse('Read', { file_path: 'b' }, '5'),
      toolUse('Grep', { pattern: 'p' }, '6'),
      toolUse('Grep', { pattern: 'q' }, '7'),
      toolUse('Bash', { command: 'c' }, '8'),
    ];
    expect(summarizeRun(run).map((segment) => segment.phrase)).toEqual([
      '3 commands',
      '1 file edited',
      '2 files read',
      '2 searches',
    ]);
    expect(runFamilies(run)).toEqual(['command', 'edit', 'read', 'search']);
  });

  it('counts distinct files for edits and falls back to calls without paths', () => {
    expect(
      summarizeRun([
        toolUse('Edit', { file_path: 'a' }, '1'),
        toolUse('Edit', { file_path: 'a' }, '2'),
        toolUse('Read', {}, '3'),
        toolUse('Read', {}, '4'),
      ]).map((segment) => segment.phrase),
    ).toEqual(['1 file edited', '2 files read']);
  });

  it('words the remaining families and counts MCP calls by their label', () => {
    expect(
      summarizeRun([
        toolUse('WebFetch', { url: 'u' }, '1'),
        toolUse('TodoWrite', {}, '2'),
        toolUse('Agent:wait', {}, '3'),
        toolUse('AskUserQuestion', {}, '4'),
        toolUse('mcp__mimir__search', {}, '5'),
        toolUse('mcp__mimir__search', {}, '6'),
      ]).map((segment) => segment.phrase),
    ).toEqual([
      '1 web lookup',
      '1 plan update',
      '1 agent call',
      '1 question',
      '2 mcp:mimir__search',
    ]);
  });
});

describe('toolResultText', () => {
  it('reads strings, content blocks, previews and objects', () => {
    expect(toolResultText(undefined)).toBe('');
    expect(toolResultText({ type: 'tool_result', tool_use_id: 'x', content: 'plain' })).toBe(
      'plain',
    );
    expect(
      toolResultText({
        type: 'tool_result',
        tool_use_id: 'x',
        content: [{ type: 'text', text: 'one' }, 'two', 7],
      }),
    ).toBe('one\ntwo');
    expect(toolResultText({ type: 'tool_result', tool_use_id: 'x', preview: 'prev' })).toBe('prev');
    expect(toolResultText({ type: 'tool_result', tool_use_id: 'x', content: { a: 1 } })).toContain(
      '"a": 1',
    );
  });
});
