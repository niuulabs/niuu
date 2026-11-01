import { describe, expect, it } from 'vitest';
import { resolveSessionResource } from './sessionResources';

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
