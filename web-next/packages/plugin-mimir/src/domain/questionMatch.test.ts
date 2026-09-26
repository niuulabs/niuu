import { describe, it, expect } from 'vitest';
import { nearestNodeForQuestion } from './questionMatch';
import { FAKE_GRAPH } from '../testing/fakeMimirService';

describe('nearestNodeForQuestion', () => {
  it('picks the node with the most shared words', () => {
    expect(nearestNodeForQuestion('why do new routes 403 on ymir?', FAKE_GRAPH.nodes)).toBe(
      '/platform/gateway-routing',
    );
  });
  it('returns null when no node title overlaps', () => {
    expect(nearestNodeForQuestion('completely unrelated question', FAKE_GRAPH.nodes)).toBeNull();
  });
  it('returns null for an empty node list', () => {
    expect(nearestNodeForQuestion('gateway routing', [])).toBeNull();
  });
  it('returns null when the question has no words', () => {
    expect(nearestNodeForQuestion('???', FAKE_GRAPH.nodes)).toBeNull();
  });
});
