import { describe, expect, it } from 'vitest';
import { presentedFilesIn, presentedResource, resolveSessionResource } from './resourcePaths';

describe('session workspace links', () => {
  it.each([
    ['README.md', '/workspace/README.md'],
    ['./docs/../README.md', '/workspace/README.md'],
    ['/home/thor/repo/src/main.ts:12:3', '/workspace/src/main.ts'],
    ['file:///home/thor/repo/docs/a%20b.md#section', '/workspace/docs/a b.md'],
    ['file://localhost/home/thor/repo/README.md', '/workspace/README.md'],
    ['</workspace/docs/guide.md>', '/workspace/docs/guide.md'],
  ])('resolves %s in the owning workspace', (href, path) => {
    expect(resolveSessionResource(href, '/home/thor/repo/')).toEqual({
      kind: 'workspace',
      path,
      name: path.split('/').at(-1),
    });
  });

  it.each([
    '',
    '#anchor',
    '//outside/a',
    'https://example.com/file',
    'javascript:alert(1)',
    'file://other-host/home/thor/repo/a',
    '/etc/passwd',
    '/home/thor/repository/a',
    '../secret',
    '%2e%2e/secret',
    '/workspace/../secret',
    '/home/thor/repo/../../secret',
    'a%00b',
    'a\\b',
    '%invalid',
    '.',
  ])('rejects unavailable or escaping link %s', (href) => {
    expect(resolveSessionResource(href, '/home/thor/repo')).toBeNull();
  });
});

describe('session home and presented files', () => {
  it('reads ~/ as the session home, never climbing out of it', () => {
    expect(resolveSessionResource('~/artifacts/plan.md')).toEqual({
      kind: 'workspace',
      path: '/home/artifacts/plan.md',
      name: 'plan.md',
    });
    expect(resolveSessionResource('~/../etc/passwd')).toBeNull();
  });

  it('finds the files a conversation presented', () => {
    expect(
      presentedFilesIn([
        { parts: [{ type: 'text' }] },
        {
          parts: [
            {
              type: 'tool_use',
              name: 'present_file',
              input: {
                file_id: 'pf_1',
                name: 'Plan',
                mime: 'text/markdown',
                path: '/home/plan.md',
              },
            },
            { type: 'tool_use', name: 'present_file', input: {} },
            { type: 'tool_use', name: 'Bash', input: { file_id: 'x' } },
          ],
        },
        { parts: [{ type: 'tool_use', name: 'present_file', input: { file_id: 'pf_2' } }] },
        {},
      ]),
    ).toEqual([
      { fileId: 'pf_1', name: 'Plan', mime: 'text/markdown', path: '/home/plan.md' },
      { fileId: 'pf_2', name: 'pf_2' },
    ]);
  });

  it('matches a link to the presented copy by path, or a bare name by the latest of that name', () => {
    const presented = [
      { fileId: 'pf_1', name: 'Plan v1', path: '/workspace/docs/plan.md' },
      { fileId: 'pf_2', name: 'Plan v2', mime: 'text/markdown', path: '/workspace/docs/plan.md' },
      { fileId: 'pf_3', name: 'Diagram' },
    ];
    const byPath = resolveSessionResource('docs/plan.md');
    expect(presentedResource('docs/plan.md', byPath, presented)).toEqual({
      kind: 'presented',
      path: 'pf_2',
      name: 'Plan v2',
      mime: 'text/markdown',
    });
    expect(
      presentedResource('plan.md', resolveSessionResource('plan.md'), presented),
    ).toMatchObject({
      path: 'pf_2',
    });
    expect(presentedResource('other.md', resolveSessionResource('other.md'), presented)).toBeNull();
    expect(presentedResource('Diagram', null, presented)).toBeNull();
  });
});
