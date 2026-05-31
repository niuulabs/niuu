import { describe, expect, it, vi } from 'vitest';
import {
  asRelationshipItems,
  asStringArray,
  asTimelineItems,
  deriveZonesFromContent,
  escapeRegExp,
  extractSection,
  inferEntityKind,
  inferPageType,
  inferTitleFromPath,
  isMissingRouteError,
  listLegacySources,
  normalizeOriginType,
  normalizeSeverity,
  normalizeZones,
  parseTimelineItems,
  stripFrontmatter,
  stripLeadingTitle,
  stripSourceFooter,
  toActivityEvent,
  toDreamCycle,
  toEmbeddingResult,
  toEntityMeta,
  toGraph,
  toGraphEdge,
  toGraphNode,
  toLintIssue,
  toLintReport,
  toMount,
  toPage,
  toPageMeta,
  toRecentWrite,
  toRegistryMount,
  toSource,
  toZone,
} from './http';

describe('mimir http helpers', () => {
  it('maps mount, registry, page, graph, and entity shapes', () => {
    expect(
      toMount({
        name: 'local',
        role: 'local',
        host: 'localhost',
        url: 'http://localhost',
        priority: 1,
        categories: ['arch'],
        status: 'healthy',
        pages: 2,
        sources: 1,
        lint_issues: 3,
        last_write: '2026-05-01',
        embedding: 'fts',
        size_kb: 12,
        desc: 'Local',
      }),
    ).toMatchObject({ lintIssues: 3, sizeKb: 12 });

    expect(
      toRegistryMount({
        id: 'mount-1',
        name: 'shared',
        kind: 'remote',
        lifecycle: 'registered',
        role: 'shared',
        url: 'https://mimir.example',
        path: '/mnt',
        categories: ['entity'],
        auth_ref: 'secret',
        default_read_priority: 5,
        enabled: true,
        health_status: 'healthy',
        health_message: 'ok',
        desc: 'Shared',
      }),
    ).toMatchObject({ authRef: 'secret', defaultReadPriority: 5 });

    const rawPage = {
      path: '/entities/alice',
      title: 'Alice',
      summary: 'Person summary',
      category: 'entity',
      updated_at: '2026-05-01',
      source_ids: ['src-1'],
      related: ['/entities/bob'],
      content: '# Alice',
      mounts: ['local'],
      entity_type: 'person',
    };
    expect(toPageMeta(rawPage)).toMatchObject({ entityType: 'person', mounts: ['local'] });
    expect(toPage({ ...rawPage, zones: [{ kind: 'assessment', text: 'Solid' }] })).toMatchObject({
      related: ['/entities/bob'],
      zones: [{ kind: 'assessment', text: 'Solid' }],
    });

    expect(toGraphNode({ id: 'n1', title: 'Node', category: 'arch', inbound_count: 2 })).toEqual({
      id: 'n1',
      title: 'Node',
      category: 'arch',
      inboundCount: 2,
    });
    expect(toGraphEdge({ source: 'a', target: 'b' })).toEqual({ source: 'a', target: 'b' });
    expect(
      toGraph({
        nodes: [{ id: 'n1', title: 'Node', category: 'arch', inbound_count: 2 }],
        edges: [{ source: 'a', target: 'b' }],
      }),
    ).toMatchObject({ nodes: [{ inboundCount: 2 }], edges: [{ source: 'a', target: 'b' }] });

    expect(
      toEntityMeta({
        path: '/entities/alice',
        title: 'Alice',
        entity_kind: 'person',
        summary: 'Person summary',
        relationship_count: 4,
      }),
    ).toMatchObject({ entityKind: 'person', relationshipCount: 4 });
  });

  it('normalizes explicit and derived zones from content', () => {
    expect(toZone({ kind: 'key-facts', items: ['one', 2, 'two'] })).toEqual({
      kind: 'key-facts',
      items: ['one', 'two'],
    });
    expect(
      toZone({
        kind: 'relationships',
        items: [{ slug: 'alice', note: 'teammate' }, { slug: 1 }, null],
      }),
    ).toEqual({
      kind: 'relationships',
      items: [{ slug: 'alice', note: 'teammate' }],
    });
    expect(
      toZone({
        kind: 'timeline',
        items: [{ date: '2026-05-01', note: 'Created', source: 'src-1' }, { note: 'skip' }],
      }),
    ).toEqual({
      kind: 'timeline',
      items: [{ date: '2026-05-01', note: 'Created', source: 'src-1' }],
    });
    expect(toZone({ kind: 'assessment', text: 'Looks good' })).toEqual({
      kind: 'assessment',
      text: 'Looks good',
    });
    expect(toZone({ kind: 'unknown' })).toBeNull();

    expect(asStringArray(['a', 1, 'b'])).toEqual(['a', 'b']);
    expect(asRelationshipItems([{ slug: 'alice', note: 'friend' }, { foo: 'bar' }])).toEqual([
      { slug: 'alice', note: 'friend' },
    ]);
    expect(asTimelineItems([{ date: '2026-05-01', note: 'Started', source: 'src-1' }, {}])).toEqual(
      [{ date: '2026-05-01', note: 'Started', source: 'src-1' }],
    );

    const explicit = normalizeZones({
      path: '/runs/demo.md',
      title: 'Demo',
      summary: 'Summary',
      category: 'runs',
      updated_at: '2026-05-01',
      source_ids: [],
      related: [],
      content: '# Demo',
      zones: [{ kind: 'assessment', text: 'Ready' }],
    });
    expect(explicit).toEqual([{ kind: 'assessment', text: 'Ready' }]);

    const derived = deriveZonesFromContent(
      `---
source_ids: [src-1]
---
# Demo

## Compiled Truth

Useful summary.

## Timeline

- 2026-05-01: Built thing. [Source: src-1]
`,
      '/runs/demo.md',
    );
    expect(derived).toEqual([
      { kind: 'assessment', text: 'Useful summary.' },
      { kind: 'timeline', items: [{ date: '2026-05-01', note: 'Built thing', source: 'src-1' }] },
    ]);

    expect(
      deriveZonesFromContent('# Demo\n\nBody copy\n\n<!-- sources:\n- src-1\n-->', '/runs/demo.md'),
    ).toEqual([{ kind: 'assessment', text: 'Body copy' }]);
  });

  it('parses markdown support helpers and path/title cleanup', () => {
    expect(stripFrontmatter('---\nfoo: bar\n---\nBody')).toBe('Body');
    expect(extractSection('## One\nA\n\n## Two\nB', '## One')).toBe('A\n');
    expect(extractSection('## One\nA', '## Missing')).toBeNull();
    expect(parseTimelineItems('- 2026-05-01: Done. [Source: src-1]\n- nope')).toEqual([
      { date: '2026-05-01', note: 'Done', source: 'src-1' },
    ]);
    expect(stripSourceFooter('Body\n<!-- sources:\n- src-1\n-->')).toBe('Body');
    expect(stripLeadingTitle('# Demo\nBody', '/runs/demo.md')).toBe('Body');
    expect(stripLeadingTitle('Demo\nBody', '/runs/demo.md')).toBe('Body');
    expect(stripLeadingTitle('Other\nBody', '/runs/demo.md')).toBe('Other\nBody');
    expect(inferTitleFromPath('/research/my_page.md')).toBe('My Page');
    expect(escapeRegExp('a+b?')).toBe('a\\+b\\?');
  });

  it('maps lint, embedding, recent write, source, dream, and activity records', async () => {
    expect(
      toLintIssue({
        id: 'L12',
        severity: 'warning',
        page_path: '/arch/overview',
        auto_fixable: true,
        message: 'Warn',
      }),
    ).toMatchObject({
      rule: 'L12',
      severity: 'warn',
      page: '/arch/overview',
      mount: 'local',
      autoFix: true,
    });
    expect(
      toLintReport({
        issues: [{ id: 'L1', severity: 'error', message: 'Broken', page: '/a' }],
        pages_checked: 1,
      }),
    ).toMatchObject({ pagesChecked: 1, summary: { error: 1, warn: 0, info: 0 } });

    expect(
      toEmbeddingResult({
        path: '/a',
        title: 'A',
        summary: 'S',
        score: 0.8,
        mount_name: 'local',
      }),
    ).toMatchObject({ mountName: 'local', score: 0.8 });
    expect(
      toRecentWrite({
        id: 'w1',
        timestamp: '2026-05-01',
        mount: 'local',
        page: '/a',
        ravn: 'r1',
        kind: 'compile',
        message: 'Compiled',
      }),
    ).toMatchObject({ kind: 'compile' });
    expect(
      toSource({
        source_id: 'src-1',
        title: 'Source',
        source_type: 'document',
        ingested_at: '2026-05-01',
        compiled_into: ['/a'],
      }),
    ).toMatchObject({ id: 'src-1', originType: 'file', ingestAgent: 'mimir' });
    expect(
      toDreamCycle({
        id: 'd1',
        timestamp: '2026-05-01',
        ravn: 'r1',
        mounts: ['local'],
        pages_updated: 2,
        entities_created: 1,
        lint_fixes: 3,
        duration_ms: 5000,
      }),
    ).toMatchObject({ pagesUpdated: 2, entitiesCreated: 1, lintFixes: 3 });
    expect(
      toActivityEvent({
        id: 'a1',
        timestamp: '2026-05-01',
        kind: 'write',
        mount: 'local',
        ravn: 'r1',
        message: 'Updated',
        page: '/a',
      }),
    ).toMatchObject({ kind: 'write', page: '/a' });

    const client = {
      get: vi.fn().mockResolvedValue([{ title: 'Legacy', ingested_at: '2026-05-01' }]),
    };
    await expect(listLegacySources(client as never)).resolves.toEqual([
      expect.objectContaining({ id: 'Legacy', title: 'Legacy' }),
    ]);
  });

  it('identifies missing-route errors and infers page/source/entity variants', () => {
    expect(isMissingRouteError({ status: 404 })).toBe(true);
    expect(isMissingRouteError({ status: 405 })).toBe(true);
    expect(isMissingRouteError({ status: 501 })).toBe(true);
    expect(isMissingRouteError(new Error('nope'))).toBe(false);

    expect(inferPageType('/entities/alice', 'arch')).toBe('entity');
    expect(inferPageType('/x/decisions/a', 'arch')).toBe('decision');
    expect(inferPageType('/x/preferences/a', 'arch')).toBe('preference');
    expect(inferPageType('/x/directives/a', 'arch')).toBe('directive');
    expect(inferPageType('/x/topic', 'arch')).toBe('topic');

    expect(normalizeSeverity('warning')).toBe('warn');
    expect(normalizeSeverity('error')).toBe('error');
    expect(normalizeOriginType('web')).toBe('web');
    expect(normalizeOriginType('document')).toBe('file');
    expect(normalizeOriginType('conversation')).toBe('chat');
    expect(normalizeOriginType('weird')).toBe('file');

    expect(inferEntityKind('/people/alice', 'Alice', 'summary')).toBe('person');
    expect(inferEntityKind('/org/acme', 'ACME', 'organisation summary')).toBe('org');
    expect(inferEntityKind('/project/x', 'X', 'summary')).toBe('project');
    expect(inferEntityKind('/component/y', 'Y', 'summary')).toBe('component');
    expect(inferEntityKind('/tech/z', 'Z', 'technology summary')).toBe('technology');
    expect(inferEntityKind('/concept/z', 'Z', 'summary')).toBe('concept');
  });
});
