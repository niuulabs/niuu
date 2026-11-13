import { describe, expect, it } from 'vitest';
import {
  filterKinds,
  formatDay,
  HUD_FILTERS,
  isRecent,
  isRecentNode,
  neighbourhood,
  recentChangeLine,
  replayMarks,
  RECENT_WINDOW_MS,
  searchNodes,
} from './hud';
import type { GraphNode, MimirGraph } from './api-types';

const NOW = Date.parse('2026-11-12T12:00:00Z');

function node(id: string, title: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    title,
    category: 'projects',
    path: `projects/${id}`,
    mount: 'project:p',
    layer: 'project:p',
    kind: 'page',
    updatedAt: '2026-01-01T00:00:00Z',
    firstSeen: '2026-01-01T00:00:00Z',
    changedAt: null,
    confidence: null,
    ...overrides,
  };
}

const GRAPH: MimirGraph = {
  nodes: [
    node('cutover', 'Booking cut-over', {
      kind: 'decision',
      firstSeen: '2026-11-03',
      changedAt: '2026-11-12',
    }),
    node('ines', 'Ines Moreau', { kind: 'person' }),
    node('lin501', 'LIN-501', { kind: 'ticket', firstSeen: '2026-11-12' }),
    node('legacy', 'Legacy booking API'),
    node('ledger', 'Payments ledger'),
    node('booking', 'Booking'),
  ],
  edges: [
    { source: 'ines', target: 'cutover', relation: 'owns', since: '2026-11-03' },
    { source: 'lin501', target: 'cutover', relation: 'moved', since: '2026-11-12' },
    { source: 'cutover', target: 'legacy', relation: 'replaces', since: '2026-11-10' },
    { source: 'legacy', target: 'ledger', relation: 'mentions', since: null },
  ],
};

describe('isRecent', () => {
  it('is true within the window ending at the reference time, false outside or after it', () => {
    expect(isRecent('2026-11-12T08:00:00Z', NOW)).toBe(true);
    expect(isRecent(new Date(NOW - RECENT_WINDOW_MS).toISOString(), NOW)).toBe(true);
    expect(isRecent(new Date(NOW - RECENT_WINDOW_MS - 1).toISOString(), NOW)).toBe(false);
    expect(isRecent('2026-11-13T00:00:00Z', NOW)).toBe(false);
  });

  it('is false for a missing or unreadable time', () => {
    expect(isRecent(null, NOW)).toBe(false);
    expect(isRecent(undefined, NOW)).toBe(false);
    expect(isRecent('', NOW)).toBe(false);
    expect(isRecent('not a date', NOW)).toBe(false);
  });

  it('counts a node as recent when it changed or first appeared this week', () => {
    expect(isRecentNode({ changedAt: '2026-11-10', firstSeen: '2026-01-01' }, NOW)).toBe(true);
    expect(isRecentNode({ changedAt: null, firstSeen: '2026-11-11' }, NOW)).toBe(true);
    expect(isRecentNode({ changedAt: '2026-09-01', firstSeen: '2026-01-01' }, NOW)).toBe(false);
  });
});

describe('searchNodes', () => {
  it('finds a name ignoring case and punctuation, best match first', () => {
    const titles = searchNodes(GRAPH.nodes, 'booking cutover').map((n) => n.title);
    expect(titles[0]).toBe('Booking cut-over');
  });

  it('ranks exact name, then prefix, then word prefixes, then substring, then path', () => {
    const titles = searchNodes(GRAPH.nodes, 'booking').map((n) => n.title);
    expect(titles).toEqual(['Booking', 'Booking cut-over', 'Legacy booking API']);
    expect(searchNodes(GRAPH.nodes, 'ines mor').map((n) => n.title)).toEqual(['Ines Moreau']);
    expect(searchNodes(GRAPH.nodes, 'oreau').map((n) => n.title)).toEqual(['Ines Moreau']);
    expect(searchNodes(GRAPH.nodes, 'projects/ledger').map((n) => n.id)).toEqual(['ledger']);
  });

  it('matches nothing for an empty or punctuation-only query, or an unknown name', () => {
    expect(searchNodes(GRAPH.nodes, '')).toEqual([]);
    expect(searchNodes(GRAPH.nodes, ' - ')).toEqual([]);
    expect(searchNodes(GRAPH.nodes, 'zeppelin')).toEqual([]);
  });

  it('returns every match, not a first few', () => {
    const many = Array.from({ length: 50 }, (_, i) => node(`n${i}`, `Runbook ${i}`));
    expect(searchNodes(many, 'runbook')).toHaveLength(50);
  });
});

describe('neighbourhood', () => {
  it('counts direct connections and the next hop out', () => {
    expect(neighbourhood(GRAPH, 'cutover')).toEqual({ connections: 3, nearby: 1 });
    expect(neighbourhood(GRAPH, 'booking')).toEqual({ connections: 0, nearby: 0 });
  });
});

describe('formatDay', () => {
  it('reads a date or a time as day and short month, in UTC', () => {
    expect(formatDay('2026-11-12')).toBe('12 Nov');
    expect(formatDay('2026-11-03T23:30:00Z')).toBe('3 Nov');
  });
});

describe('recentChangeLine', () => {
  it('names the links introduced this week, newest first, from the focus’s side', () => {
    expect(recentChangeLine(GRAPH, 'cutover', NOW)).toBe(
      'This week: LIN-501 moved it (12 Nov); replaces Legacy booking API (10 Nov).',
    );
  });

  it('says when a page first appeared this week with no new links', () => {
    const graph: MimirGraph = { nodes: [node('x', 'X', { firstSeen: '2026-11-11' })], edges: [] };
    expect(recentChangeLine(graph, 'x', NOW)).toBe('New this week: first seen 11 Nov.');
  });

  it('says when a page changed this week with no new links', () => {
    const graph: MimirGraph = { nodes: [node('x', 'X', { changedAt: '2026-11-09' })], edges: [] };
    expect(recentChangeLine(graph, 'x', NOW)).toBe('This week: changed 9 Nov.');
  });

  it('is null when nothing changed recently, or the node is unknown', () => {
    expect(recentChangeLine(GRAPH, 'ines', Date.parse('2026-12-30T00:00:00Z'))).toBeNull();
    expect(recentChangeLine(GRAPH, 'nope', NOW)).toBeNull();
  });

  it('reads an untyped link as "linked"', () => {
    const graph: MimirGraph = {
      nodes: [node('a', 'A'), node('b', 'B')],
      edges: [{ source: 'a', target: 'b', relation: 'mentions', since: '2026-11-12' }],
    };
    expect(recentChangeLine(graph, 'b', NOW)).toBe('This week: A linked it (12 Nov).');
  });
});

describe('replayMarks', () => {
  it('lists the day first seen and each link’s day, one mark per day, oldest first', () => {
    expect(replayMarks(GRAPH, 'cutover', NOW)).toEqual([
      { date: '2026-11-03', label: 'first seen, owns', recent: false },
      { date: '2026-11-10', label: 'replaces', recent: true },
      { date: '2026-11-12', label: 'moved', recent: true },
    ]);
  });

  it('is empty for an unknown node, and skips undated links', () => {
    expect(replayMarks(GRAPH, 'nope', NOW)).toEqual([]);
    expect(replayMarks(GRAPH, 'ledger', NOW)).toEqual([
      { date: '2026-01-01', label: 'first seen', recent: false },
    ]);
  });
});

describe('filters', () => {
  it('offers Everything, People, Decisions and Tickets', () => {
    expect(HUD_FILTERS.map((f) => f.label)).toEqual([
      'Everything',
      'People',
      'Decisions',
      'Tickets',
    ]);
  });

  it('maps a filter to the kinds it keeps', () => {
    expect(filterKinds('all')).toBeNull();
    expect([...filterKinds('people')!]).toEqual(['person']);
    expect([...filterKinds('decisions')!]).toEqual(['decision']);
    expect([...filterKinds('tickets')!]).toEqual(['ticket']);
  });
});
