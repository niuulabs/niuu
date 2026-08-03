import { describe, it, expect } from 'vitest';
import { CALM_HIDDEN_LAYERS } from '../application/useObservatoryStore';
import {
  EDGE_LAYERS,
  EDGE_LAYER_LABELS,
  countEdgesByLayer,
  edgeLayer,
  visibleEdges,
} from './edgeLayer';
import type { EdgeLayer } from './edgeLayer';
import type { EdgeRelationType, TopologyEdge } from './index';

const ALL_RELATIONS: EdgeRelationType[] = [
  'contains',
  'manages',
  'uses',
  'reads',
  'writes',
  'routes_to',
  'exposes',
  'observes',
  'signals_to',
  'member_of',
];

function edge(relationType?: EdgeRelationType): TopologyEdge {
  return {
    id: `e-${relationType ?? 'none'}`,
    sourceId: 'a',
    targetId: 'b',
    kind: 'solid',
    relationType,
  };
}

describe('the model path', () => {
  it('keeps a caller and its gateway on the same layer', () => {
    // `uses` is an agent reaching its gateway and `routes_to` is that gateway
    // reaching the model. Split across layers, the calm view drew the model
    // being called and hid who called it.
    expect(edgeLayer({ relationType: 'uses' })).toBe('inference');
    expect(edgeLayer({ relationType: 'routes_to' })).toBe('inference');
  });

  it('is not hidden by the view the Observatory opens in', () => {
    expect(CALM_HIDDEN_LAYERS).not.toContain(edgeLayer({ relationType: 'uses' }));
  });
});

describe('edgeLayer', () => {
  it('assigns a layer to every relation in the taxonomy', () => {
    for (const relation of ALL_RELATIONS) {
      expect(EDGE_LAYERS).toContain(edgeLayer(edge(relation)));
    }
  });

  it('groups the relations by the question they answer', () => {
    expect(edgeLayer(edge('member_of'))).toBe('mesh');
    expect(edgeLayer(edge('reads'))).toBe('memory');
    expect(edgeLayer(edge('writes'))).toBe('memory');
    expect(edgeLayer(edge('routes_to'))).toBe('inference');
    expect(edgeLayer(edge('observes'))).toBe('observability');
    expect(edgeLayer(edge('signals_to'))).toBe('signals');
    expect(edgeLayer(edge('manages'))).toBe('platform');
  });

  it('treats an undeclared relation as platform wiring rather than hiding it', () => {
    expect(edgeLayer(edge(undefined))).toBe('platform');
  });

  it('labels every layer', () => {
    for (const layer of EDGE_LAYERS) {
      expect(EDGE_LAYER_LABELS[layer]).toBeTruthy();
    }
  });
});

describe('visibleEdges', () => {
  const edges = [edge('member_of'), edge('writes'), edge('routes_to'), edge('manages')];

  it('returns everything when nothing is hidden', () => {
    expect(visibleEdges(edges, new Set())).toHaveLength(edges.length);
  });

  it('drops only the hidden layers', () => {
    const hidden = new Set<EdgeLayer>(['memory']);
    const shown = visibleEdges(edges, hidden);
    expect(shown.map((e) => e.relationType)).toEqual(['member_of', 'routes_to', 'manages']);
  });

  it('can hide everything', () => {
    expect(visibleEdges(edges, new Set(EDGE_LAYERS))).toEqual([]);
  });

  it('does not mutate the input', () => {
    const before = edges.length;
    visibleEdges(edges, new Set<EdgeLayer>(['mesh']));
    expect(edges).toHaveLength(before);
  });
});

describe('countEdgesByLayer', () => {
  it('counts each layer and reports zero for the empty ones', () => {
    const counts = countEdgesByLayer([edge('member_of'), edge('reads'), edge('writes')]);
    expect(counts.mesh).toBe(1);
    expect(counts.memory).toBe(2);
    expect(counts.inference).toBe(0);
    expect(counts.signals).toBe(0);
  });

  it('returns a zero for every layer given no edges', () => {
    const counts = countEdgesByLayer([]);
    for (const layer of EDGE_LAYERS) expect(counts[layer]).toBe(0);
  });
});
