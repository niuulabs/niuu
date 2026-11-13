import { describe, expect, it } from 'vitest';
import { buildMockKnowledgeGraph, MOCK_BOOKING_LAYER, MOCK_ORG_LAYER } from './mockKnowledgeGraph';
import { createMimirMockAdapter } from './mock';
import { searchNodes, recentChangeLine } from '../domain/hud';
import type { MimirGraph } from '../domain/api-types';

const NOW = Date.parse('2026-11-12T12:00:00Z');

const BASE: MimirGraph = {
  nodes: ['a', 'b', 'c'].map((id) => ({
    id,
    title: id.toUpperCase(),
    category: 'arch',
    path: `/${id}`,
    mount: id === 'c' ? 'shared' : 'local',
    kind: 'topic',
    updatedAt: '2026-04-19T00:00:00Z',
    firstSeen: '2026-04-01T00:00:00Z',
    confidence: 'high',
  })),
  edges: [
    { source: 'a', target: 'b', relation: 'depends_on' },
    { source: 'a', target: 'c', relation: 'explains' },
  ],
};

describe('buildMockKnowledgeGraph', () => {
  const graph = buildMockKnowledgeGraph(BASE, NOW);

  it('turns every mount into a layer and adds the org and booking layers', () => {
    expect(graph.layers).toEqual([
      { id: 'local', name: 'Local' },
      { id: MOCK_ORG_LAYER, name: 'Org' },
      { id: MOCK_BOOKING_LAYER, name: 'Booking platform' },
      { id: 'shared', name: 'Shared' },
    ]);
    expect(graph.nodes.find((n) => n.id === 'c')).toMatchObject({ layer: 'shared' });
  });

  it('has a booking cut-over to fly to, changed today, with dated links', () => {
    const [cutover] = searchNodes(graph.nodes, 'booking cut-over');
    expect(cutover?.title).toBe('Booking cut-over');
    expect(cutover?.changedAt).toBe(new Date(NOW).toISOString());
    expect(recentChangeLine(graph, cutover!.id, NOW)).toContain('LIN-501 moved it');
    const relations = graph.edges.filter(
      (e) => e.source === cutover!.id || e.target === cutover!.id,
    );
    expect(relations.map((e) => e.relation)).toEqual([
      'owns',
      'planned in',
      'moved',
      'changes',
      'replaces',
      'waits for',
      'covered by',
    ]);
  });

  it('bridges the booking cluster into the cloud through the best-connected pages', () => {
    expect(graph.edges.find((e) => e.relation === 'depends on')?.target).toBe('a');
    expect(graph.edges.find((e) => e.relation === 'mentions')?.target).toBeDefined();
    expect(graph.unresolved).toHaveLength(1);
  });

  it('every edge joins two nodes of the graph', () => {
    const ids = new Set(graph.nodes.map((n) => n.id));
    for (const edge of graph.edges) {
      expect(ids.has(edge.source)).toBe(true);
      expect(ids.has(edge.target)).toBe(true);
    }
  });

  it('a base graph with no links gets no bridges', () => {
    const bare = buildMockKnowledgeGraph({ nodes: BASE.nodes, edges: [] }, NOW);
    expect(bare.edges.some((e) => e.relation === 'depends on')).toBe(false);
  });
});

describe('createMimirMockAdapter — pages.getKnowledgeGraph', () => {
  it('serves every layer, or narrows to the layers named', async () => {
    const adapter = createMimirMockAdapter();
    const all = await adapter.pages.getKnowledgeGraph();
    expect(all.layers.map((l) => l.id)).toContain(MOCK_BOOKING_LAYER);
    const booking = await adapter.pages.getKnowledgeGraph({ layers: [MOCK_BOOKING_LAYER] });
    expect(booking.layers).toEqual([{ id: MOCK_BOOKING_LAYER, name: 'Booking platform' }]);
    expect(booking.nodes.every((n) => n.layer === MOCK_BOOKING_LAYER)).toBe(true);
    const ids = new Set(booking.nodes.map((n) => n.id));
    expect(booking.edges.every((e) => ids.has(e.source) && ids.has(e.target))).toBe(true);
    expect(booking.unresolved).toHaveLength(1);
  });
});
